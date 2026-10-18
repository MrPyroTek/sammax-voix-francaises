using System.IO.Compression;
using System.Text;

byte[] Key = new byte[] {
    0x92, 0xCA, 0x9A, 0x81, 0x85, 0xE4, 0x64, 0x73, 0xA2, 0xBF, 0xD6, 0xD1, 0x7F, 0xC6, 0xCB, 0x88,
    0x99, 0x5A, 0x80, 0xD8, 0xAA, 0xC2, 0x97, 0xE7, 0x96, 0x51, 0x9F, 0xA8, 0x9A, 0xD9, 0xAE, 0x95,
    0xD7, 0x76, 0x62, 0x7F, 0xB4, 0xC4, 0xA6, 0xB9, 0xD6, 0xEC, 0xA9, 0x9C, 0x67, 0x85, 0xB3, 0xDC,
    0x92, 0xC4, 0x9E, 0x64, 0xA0, 0xA2, 0x92
};

if (args.Length >= 1 && args[0] == "--voxdec")
{
    byte[] key = Convert.FromHexString(args[2]);
    byte[] data = File.ReadAllBytes(args[1]);
    int ver = args.Length > 3 ? int.Parse(args[3]) : 2;
    var fish = new BlowFishCS.BlowFish(key, ver);
    byte[] dec = fish.Crypt_ECB(data, ver, true);
    Console.WriteLine(BitConverter.ToString(dec, 0, Math.Min(48, dec.Length)));
    Console.WriteLine(Encoding.ASCII.GetString(dec));
    return;
}

if (args.Length >= 1 && args[0] == "--old")
{
    ListOld(args[1]);
    return;
}

if (args.Length >= 4 && args[0] == "--langdb")
{
    ListOld(args[1], Convert.FromHexString(args[2]), args[3]);
    return;
}

if (args.Length >= 4 && args[0] == "--plainvox")
{
    var keys = args.Skip(3).Select(Convert.FromHexString).ToArray();
    PlainVoxDir(args[1], args[2], keys);
    return;
}

if (args.Length < 1)
{
    Console.Error.WriteLine("usage: TtarchList <archive> [extract-ext] [out-dir]");
    return;
}

string path = args[0];
string extractExt = args.Length > 1 ? args[1] : null;
string outDir = args.Length > 2 ? args[2] : null;

var arc = ReadArchive(path, Key);
var groups = arc.Files.GroupBy(f =>
{
    int dot = f.Name.LastIndexOf('.');
    return dot >= 0 ? f.Name.Substring(dot).ToLowerInvariant() : "";
}).OrderByDescending(g => g.Count());

Console.WriteLine($"files={arc.Files.Count} chunk={arc.ChunkSize} compressed={arc.IsCompressed} encrypted={arc.IsEncrypted}");
foreach (var g in groups)
    Console.WriteLine($"  {g.Key} {g.Count()}");

foreach (var f in arc.Files.Where(f => !f.Name.EndsWith(".wav", StringComparison.OrdinalIgnoreCase)).Take(40))
    Console.WriteLine($"  sample {f.Name} {f.Size}");

if (extractExt != null && outDir != null)
{
    Directory.CreateDirectory(outDir);
    int n = 0;
    foreach (var f in arc.Files.Where(f =>
        extractExt.StartsWith("name:", StringComparison.OrdinalIgnoreCase)
            ? f.Name.Contains(extractExt.Substring(5), StringComparison.OrdinalIgnoreCase)
            : f.Name.EndsWith(extractExt, StringComparison.OrdinalIgnoreCase)))
    {
        byte[] data = Extract(arc, f, Key);
        string safe = f.Name.Replace('/', Path.DirectorySeparatorChar).Replace('\\', Path.DirectorySeparatorChar);
        string dest = Path.Combine(outDir, safe);
        Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
        File.WriteAllBytes(dest, data);
        n++;
        if (n <= 5) Console.WriteLine($"extracted {f.Name} {data.Length}");
    }
    Console.WriteLine($"extracted_count={n}");
}

static Archive ReadArchive(string path, byte[] key)
{
    using var fs = File.OpenRead(path);
    using var br = new BinaryReader(fs);
    var arc = new Archive();
    string magic = Encoding.ASCII.GetString(br.ReadBytes(4));
    arc.IsCompressed = magic != "NCTT";
    arc.IsEncrypted = magic == "ECTT" || magic == "eCTT";
    int algorithm = (magic == "eCTT" || magic == "zCTT") ? 2 : 1;

    if (!arc.IsCompressed)
        throw new Exception("uncompressed listing already handled elsewhere");

    if (magic == "eCTT" || magic == "zCTT")
        br.ReadInt32();

    arc.ChunkSize = br.ReadUInt32();
    int blocksCount = br.ReadInt32();
    arc.Blocks = new ulong[blocksCount];
    ulong val1 = br.ReadUInt64();
    for (int i = 0; i < blocksCount; i++)
    {
        ulong val2 = br.ReadUInt64();
        arc.Blocks[i] = val2 - val1;
        val1 = val2;
    }
    arc.DataOffset = (ulong)fs.Position;
    arc.Algorithm = algorithm;
    arc.Path = path;

    byte[] first = ReadBlock(br, arc, 0, key);
    int suboff = 0;
    uint filesCount;
    uint nameSize;
    using (var ms = new MemoryStream(first))
    using (var mbr = new BinaryReader(ms))
    {
        string sub = Encoding.ASCII.GetString(mbr.ReadBytes(4));
        suboff += 4;
        if (sub == "3ATT")
        {
            mbr.ReadInt32();
            suboff += 4;
        }
        nameSize = mbr.ReadUInt32();
        suboff += 4;
        filesCount = mbr.ReadUInt32();
        suboff += 4;
        arc.FilesOffset = (ulong)suboff + (28UL * filesCount) + nameSize;
    }

    byte[] headerBytes = first;
    if (arc.FilesOffset > (ulong)first.Length)
    {
        int index = (int)(arc.FilesOffset / arc.ChunkSize) + 1;
        using var ms = new MemoryStream();
        fs.Position = (long)arc.DataOffset;
        for (int i = 0; i < index; i++)
        {
            byte[] chunk = ReadBlock(br, arc, i, key);
            ms.Write(chunk, 0, chunk.Length);
        }
        headerBytes = ms.ToArray();
    }

    arc.Files = new List<Entry>();
    using (var ms = new MemoryStream(headerBytes))
    using (var mbr = new BinaryReader(ms))
    {
        ms.Position = suboff;
        for (int i = 0; i < filesCount; i++)
        {
            var e = new Entry();
            e.Crc = mbr.ReadUInt64();
            e.Offset = mbr.ReadUInt64();
            e.Size = mbr.ReadInt32();
            mbr.ReadInt32();
            ushort nameBlock = mbr.ReadUInt16();
            ushort nameOff = mbr.ReadUInt16();
            long pos = ms.Position;
            long nameOffset = suboff + (28L * filesCount) + nameOff + (nameBlock * 0x10000L);
            ms.Position = nameOffset;
            using var nms = new MemoryStream();
            int b;
            while ((b = ms.ReadByte()) > 0)
                nms.WriteByte((byte)b);
            e.Name = Encoding.ASCII.GetString(nms.ToArray());
            arc.Files.Add(e);
            ms.Position = pos;
        }
    }
    return arc;
}

static byte[] ReadBlock(BinaryReader br, Archive arc, int index, byte[] key)
{
    ulong cOff = 0;
    for (int c = 0; c < index; c++) cOff += arc.Blocks[c];
    br.BaseStream.Position = (long)(arc.DataOffset + cOff);
    byte[] tmp = br.ReadBytes((int)arc.Blocks[index]);
    if (arc.IsEncrypted)
    {
        var dec = new BlowFishCS.BlowFish(key, 7);
        tmp = dec.Crypt_ECB(tmp, 7, true);
    }
    if (tmp.Length == arc.ChunkSize)
        return tmp;
    return Deflate(tmp);
}

static byte[] Extract(Archive arc, Entry file, byte[] key)
{
    using var fs = File.OpenRead(arc.Path);
    using var br = new BinaryReader(fs);
    if (!arc.IsCompressed)
    {
        fs.Position = (long)arc.FilesOffset + (long)file.Offset;
        return br.ReadBytes(file.Size);
    }
    int index = (int)((arc.FilesOffset + file.Offset) / arc.ChunkSize);
    int index2 = (int)((arc.FilesOffset + file.Offset + (ulong)file.Size) / arc.ChunkSize);
    using var ms = new MemoryStream();
    for (int c = index; c <= index2; c++)
    {
        byte[] tmp = ReadBlock(br, arc, c, key);
        ms.Write(tmp, 0, tmp.Length);
    }
    byte[] block = ms.ToArray();
    ulong dOff = (arc.FilesOffset + file.Offset) - (arc.ChunkSize * (ulong)index);
    byte[] result = new byte[file.Size];
    Buffer.BlockCopy(block, (int)dOff, result, 0, file.Size);
    return result;
}

static byte[] Deflate(byte[] bytes)
{
    using var input = new MemoryStream(bytes);
    using var def = new DeflateStream(input, CompressionMode.Decompress);
    using var output = new MemoryStream();
    def.CopyTo(output);
    return output.ToArray();
}

static void MetaCrypt(byte[] file, byte[] key, int version, bool decrypt)
{
    if (file.Length < 4) return;
    uint fileType = BitConverter.ToUInt32(file, 0);
    long blockSize = 0, blockCrypt = 0, blockClean = 0;
    switch (fileType)
    {
        case 0xFB4A1764:
        case 0xEB794091:
        case 0x64AFDEFB:
            blockSize = 0x80; blockCrypt = 0x20; blockClean = 0x50; break;
        case 0x64AFDEAA:
            blockSize = 0x100; blockCrypt = 0x8; blockClean = 0x18; break;
        case 0x4D424553:
            blockSize = 0x40; blockCrypt = 0x40; blockClean = 0x64; break;
        default:
            return;
    }
    long blocks = (file.Length - 4) / blockSize;
    uint p = (uint)file.Length;
    uint l = p + (uint)file.Length;
    long poz = 0;
    for (long i = 0; i < blocks; i++)
    {
        if (p >= l) break;
        byte[] block = new byte[blockSize];
        Buffer.BlockCopy(file, 4 + (int)poz, block, 0, (int)blockSize);
        if (i % blockCrypt == 0)
        {
            var enc = new BlowFishCS.BlowFish(key, version);
            block = enc.Crypt_ECB(block, version, decrypt);
        }
        else if (!((i % blockClean == 0) && i > 0))
        {
            for (int n = 0; n < block.Length; n++) block[n] ^= 0xff;
        }
        Buffer.BlockCopy(block, 0, file, 4 + (int)poz, (int)blockSize);
        p += (uint)blockSize;
        poz += blockSize;
    }
}

static void PlainVoxDir(string inDir, string outDir, byte[][] keys)
{
    Directory.CreateDirectory(outDir);
    int decrypted = 0, failed = 0, skipped = 0;
    foreach (string file in Directory.EnumerateFiles(inDir, "*.vox", SearchOption.AllDirectories))
    {
        byte[] data = File.ReadAllBytes(file);
        if (!VoxNeedsDecrypt(data))
        {
            skipped++;
            continue;
        }
        byte[] key = FindVoxKey(data, keys);
        if (key == null)
        {
            failed++;
            Console.WriteLine("nokey " + Path.GetFileName(file));
            continue;
        }
        DecryptVoxPackets(data, key);
        File.WriteAllBytes(Path.Combine(outDir, Path.GetFileName(file)), data);
        decrypted++;
    }
    Console.WriteLine($"plainvox decrypted={decrypted} failed={failed} skipped={skipped}");
}

static bool VoxNeedsDecrypt(byte[] data)
{
    if (!TryVoxPackets(data, out bool encrypted, out int count, out _, out _))
        return false;
    return encrypted && count > 63;
}

static byte[] FindVoxKey(byte[] data, byte[][] keys)
{
    if (!TryVoxPackets(data, out _, out int count, out int[] positions, out int[] sizes))
        return null;
    if (count < 1 || sizes[0] < 32)
        return null;
    foreach (byte[] key in keys)
    {
        var fish = new BlowFishCS.BlowFish(key, 2);
        byte[] probe = new byte[32];
        Buffer.BlockCopy(data, positions[0], probe, 0, 32);
        fish.Crypt_ECB(probe, 2, true);
        if (Encoding.ASCII.GetString(probe).Contains("Encoded with Speex"))
            return key;
    }
    return null;
}

static void DecryptVoxPackets(byte[] data, byte[] key)
{
    if (!TryVoxPackets(data, out _, out int count, out int[] positions, out int[] sizes))
        return;
    var fish = new BlowFishCS.BlowFish(key, 2);
    for (int i = 0; i < count; i++)
    {
        if (i % 64 != 0)
            continue;
        int n = sizes[i] / 8 * 8;
        if (n <= 0)
            continue;
        byte[] block = new byte[n];
        Buffer.BlockCopy(data, positions[i], block, 0, n);
        fish.Crypt_ECB(block, 2, true);
        Buffer.BlockCopy(block, 0, data, positions[i], n);
    }
}

static bool TryVoxPackets(byte[] data, out bool encrypted, out int count, out int[] positions, out int[] sizes)
{
    encrypted = false;
    count = 0;
    positions = null;
    sizes = null;
    if (data.Length < 32 || Encoding.ASCII.GetString(data, 0, 4) != "NIBM")
        return false;
    int nameLen = BitConverter.ToInt32(data, 8);
    int pos = 12 + nameLen + 4;
    if (nameLen < 0 || pos + 21 > data.Length)
        return false;
    encrypted = data[pos] == (byte)'1';
    pos += 1 + 4;
    int dataBlock = BitConverter.ToInt32(data, pos); pos += 4;
    pos += 12; // frame, rate, mode
    pos += 4;  // table size
    count = BitConverter.ToInt32(data, pos); pos += 4;
    if (count <= 0 || count > 100000 || pos + count * 4 > data.Length)
        return false;
    var offsets = new int[count];
    for (int i = 0; i < count; i++)
    {
        offsets[i] = BitConverter.ToInt32(data, pos);
        pos += 4;
    }
    int start = pos;
    if (start != data.Length - dataBlock)
        start = pos;
    sizes = new int[count];
    positions = new int[count];
    for (int i = 0; i < count; i++)
    {
        sizes[i] = i == count - 1
            ? data.Length - (offsets[i] + start)
            : offsets[i + 1] - offsets[i];
        positions[i] = start + offsets[i];
        if (sizes[i] < 0 || positions[i] < 0 || positions[i] + sizes[i] > data.Length)
            return false;
    }
    return true;
}

static void ListOld(string path, byte[] key = null, string outDir = null)
{
    key ??= new byte[] {
        0x92, 0xCA, 0x9A, 0x81, 0x85, 0xE4, 0x64, 0x73, 0xA3, 0xBF, 0xD6, 0xD1, 0x7F, 0xC6, 0xCB, 0x88,
        0x99, 0x5B, 0x80, 0xD8, 0xAA, 0xC2, 0x97, 0xE7, 0x96, 0x51, 0xA0, 0xA8, 0x9A, 0xD9, 0xAE, 0x95,
        0xD7, 0x76, 0x62, 0x80, 0xB4, 0xC4, 0xA6, 0xB9, 0xD6, 0xEC, 0xA9, 0x9C, 0x68, 0x85, 0xB3, 0xDC,
        0x92, 0xC4, 0x9E, 0x64, 0xA0, 0xA3, 0x92
    };
    using var fs = File.OpenRead(path);
    using var br = new BinaryReader(fs);
    int version = br.ReadInt32();
    int encryption = br.ReadInt32();
    int two = br.ReadInt32();
    Console.WriteLine($"version={version} encryption={encryption} two={two}");
    int val = 0;
    int[] blocks = null;
    if (version > 2)
    {
        val = br.ReadInt32();
        int countBlocks = br.ReadInt32();
        if (val == 2)
        {
            blocks = new int[countBlocks];
            for (int k = 0; k < countBlocks; k++) blocks[k] = br.ReadInt32();
        }
        br.ReadUInt32();
        if (version >= 4)
        {
            br.ReadInt32();
            br.ReadInt32();
            if (version >= 7)
            {
                br.ReadInt32();
                br.ReadInt32();
                br.ReadInt32();
                if (version > 7)
                {
                    br.ReadByte();
                    if (version == 9) br.ReadUInt32();
                }
            }
        }
    }
    int headerSize = br.ReadInt32();
    int cHeaderSize = -1;
    if (version >= 7 && val == 2) cHeaderSize = br.ReadInt32();
    byte[] header = (version >= 7 && val == 2) ? br.ReadBytes(cHeaderSize) : br.ReadBytes(headerSize);
    long dataAt = fs.Position;
    Console.WriteLine($"headerBytes={header.Length} dataAt={dataAt} blocks={(blocks == null ? 0 : blocks.Length)}");
    if (version >= 7 && val == 2)
    {
        try { header = Deflate(header); Console.WriteLine("header deflated " + header.Length); }
        catch (Exception ex) { Console.WriteLine("deflate failed " + ex.Message); }
    }
    if (encryption == 1)
    {
        var dec = new BlowFishCS.BlowFish(key, version);
        header = dec.Crypt_ECB(header, version, true);
    }
    Console.WriteLine("header head " + BitConverter.ToString(header, 0, Math.Min(32, header.Length)));
    using var ms = new MemoryStream(header);
    using var mbr = new BinaryReader(ms);
    int dirs = mbr.ReadInt32();
    Console.WriteLine("dirs=" + dirs);
    if (dirs < 0 || dirs > 10000) return;
    for (int d = 0; d < dirs; d++)
    {
        int nameLen = mbr.ReadInt32();
        if (nameLen < 0 || nameLen > 4096 || ms.Position + nameLen > ms.Length) { Console.WriteLine("bad dir name " + nameLen); return; }
        string name = Encoding.ASCII.GetString(mbr.ReadBytes(nameLen));
        Console.WriteLine(" dir " + name);
    }
    int filesCount = mbr.ReadInt32();
    Console.WriteLine("files=" + filesCount);
    var ext = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
    int shown = 0;
    for (int f = 0; f < filesCount; f++)
    {
        int nameLen = mbr.ReadInt32();
        if (nameLen <= 0 || nameLen > 4096 || ms.Position + nameLen + 8 > ms.Length) { Console.WriteLine("bad file at " + f + " len " + nameLen); return; }
        string name = Encoding.ASCII.GetString(mbr.ReadBytes(nameLen));
        mbr.ReadInt32();
        uint off = mbr.ReadUInt32();
        int size = mbr.ReadInt32();
        string e = Path.GetExtension(name);
        if (!ext.ContainsKey(e)) ext[e] = 0;
        ext[e]++;
        if (e.Equals(".dlg", StringComparison.OrdinalIgnoreCase) || e.Equals(".langdb", StringComparison.OrdinalIgnoreCase) || e.Equals(".txt", StringComparison.OrdinalIgnoreCase))
        {
            Console.WriteLine($"  {name} off={off} size={size}");
            shown++;
        }
        if (name.Equals("french.langdb", StringComparison.OrdinalIgnoreCase) || name.Equals("street_sam_max.dlg", StringComparison.OrdinalIgnoreCase))
        {
            long save = fs.Position;
            fs.Position = dataAt + off;
            byte[] blob = br.ReadBytes(size);
            fs.Position = save;
            MetaCrypt(blob, key, version, true);
            string destDir = outDir ?? @"C:\Users\lbpla\Documents\sammaxfr\extracted\old101";
            Directory.CreateDirectory(destDir);
            File.WriteAllBytes(Path.Combine(destDir, name), blob);
            Console.WriteLine("  wrote " + name + " magic " + BitConverter.ToString(blob, 0, Math.Min(8, blob.Length)));
        }
    }
    foreach (var kv in ext.OrderByDescending(k => k.Value))
        Console.WriteLine($"  ext {kv.Key} {kv.Value}");
}

sealed class Archive
{
    public string Path;
    public bool IsCompressed;
    public bool IsEncrypted;
    public int Algorithm;
    public uint ChunkSize;
    public ulong[] Blocks;
    public ulong DataOffset;
    public ulong FilesOffset;
    public List<Entry> Files;
}

sealed class Entry
{
    public ulong Crc;
    public ulong Offset;
    public int Size;
    public string Name;
}
