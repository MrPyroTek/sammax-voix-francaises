using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using Microsoft.Win32;

internal static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string path = args.Length >= 2 ? args[1].Trim('"') : "";
        string auto = args.Length >= 1 && (args[0] == "--install" || args[0] == "--restore") ? args[0] : null;
        Application.Run(new MainForm(path, auto));
    }
}

internal sealed class MainForm : Form
{
    const long EnglishMaxBytes = 120L * 1024 * 1024;
    static readonly int[] Episodes = { 101, 102, 103, 104, 105, 106 };

    readonly TextBox _path = new TextBox();
    readonly Button _browse = new Button();
    readonly Button _install = new Button();
    readonly Button _restore = new Button();
    readonly ProgressBar _bar = new ProgressBar();
    readonly TextBox _log = new TextBox();
    readonly string _auto;

    public MainForm(string initialPath, string auto)
    {
        _auto = auto;
        Text = "Voix françaises — Sam & Max Save the World";
        Font = new Font("Segoe UI", 10f);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(680, 460);

        var title = new Label
        {
            Text = "Voix françaises",
            Font = new Font("Segoe UI", 16f, FontStyle.Bold),
            AutoSize = true,
            Location = new Point(20, 16),
        };
        var hint = new Label
        {
            Text = "Choisis le dossier du jeu. Les voix anglaises sont gardées dans Archives\\EnglishVoiceBackup, et tu peux les remettre ensuite.",
            Location = new Point(22, 52),
            Size = new Size(636, 40),
        };
        var folderLabel = new Label { Text = "Dossier du jeu", AutoSize = true, Location = new Point(22, 104) };
        _path.Location = new Point(22, 128);
        _path.Size = new Size(520, 27);
        _path.TextChanged += (_, _) => RefreshButtons();
        _browse.Text = "Parcourir…";
        _browse.Location = new Point(552, 126);
        _browse.Size = new Size(106, 30);
        _browse.Click += (_, _) => Browse();
        _install.Text = "Installer les voix françaises";
        _install.Location = new Point(22, 172);
        _install.Size = new Size(310, 38);
        _install.Click += async (_, _) => await RunJob(install: true);
        _restore.Text = "Remettre les voix anglaises";
        _restore.Location = new Point(348, 172);
        _restore.Size = new Size(310, 38);
        _restore.Click += async (_, _) => await RunJob(install: false);
        _bar.Location = new Point(22, 224);
        _bar.Size = new Size(636, 18);
        _bar.Maximum = 1000;
        _log.Location = new Point(22, 254);
        _log.Size = new Size(636, 184);
        _log.Multiline = true;
        _log.ReadOnly = true;
        _log.ScrollBars = ScrollBars.Vertical;
        _log.BackColor = Color.White;

        Controls.AddRange(new Control[] { title, hint, folderLabel, _path, _browse, _install, _restore, _bar, _log });
        FormClosed += (_, _) => CleanupStage();
        Load += async (_, _) =>
        {
            _path.Text = string.IsNullOrWhiteSpace(initialPath) ? FindGame() : initialPath;
            if (string.IsNullOrWhiteSpace(_path.Text))
                Write("Le jeu n'a pas été trouvé tout seul. Indique le dossier qui contient SamMax100.exe.");
            else
                Write("Jeu trouvé.");
            if (!PayloadReady())
                Write("Les fichiers de voix de l'installeur sont introuvables.");
            RefreshButtons();
            if (_auto == "--install") await RunJob(true);
            else if (_auto == "--restore") await RunJob(false);
        };
    }

    void Browse()
    {
        using var dialog = new FolderBrowserDialog
        {
            Description = "Dossier qui contient SamMax100.exe",
            UseDescriptionForTitle = true,
        };
        if (Directory.Exists(_path.Text)) dialog.SelectedPath = _path.Text;
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        string chosen = dialog.SelectedPath;
        string nested = Path.Combine(chosen, "Sam & Max Save the World");
        if (!File.Exists(Path.Combine(chosen, "SamMax100.exe")) && File.Exists(Path.Combine(nested, "SamMax100.exe")))
            chosen = nested;
        _path.Text = chosen;
    }

    void RefreshButtons()
    {
        bool ok = IsGameDir(_path.Text) && PayloadReady();
        _install.Enabled = ok;
        _restore.Enabled = IsGameDir(_path.Text);
    }

    static bool IsGameDir(string path)
    {
        return !string.IsNullOrWhiteSpace(path) && File.Exists(Path.Combine(path, "SamMax100.exe"));
    }

    async Task RunJob(bool install)
    {
        if (Process.GetProcessesByName("SamMax100").Length > 0)
        {
            MessageBox.Show(this, "Ferme le jeu, puis relance l'installation.", Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        if (!IsGameDir(_path.Text))
        {
            MessageBox.Show(this, "Ce dossier ne contient pas SamMax100.exe.", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        SetBusy(true);
        string game = _path.Text;
        try
        {
            await Task.Run(() =>
            {
                if (install) Install(game);
                else Restore(game);
            });
        }
        catch (UnauthorizedAccessException)
        {
            SetBusy(false);
            if (AskElevate(install, game)) Close();
            return;
        }
        catch (Exception ex)
        {
            Write(ex.Message);
            MessageBox.Show(this, ex.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        SetBusy(false);
    }

    bool AskElevate(bool install, string game)
    {
        var answer = MessageBox.Show(this,
            "Windows bloque l'écriture dans le dossier du jeu. Relancer l'installeur en administrateur ?",
            Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
        if (answer != DialogResult.Yes) return false;
        try
        {
            Write("Préparation du redémarrage administrateur…");
            string stagedExe = Path.Combine(StageDir, "SamMaxVoixFR.exe");
            Directory.CreateDirectory(StageDir);
            StageFile(Environment.ProcessPath, stagedExe);
            foreach (int ep in Episodes)
            {
                string src = Payload(ep) ?? throw new InvalidOperationException("Fichiers de voix manquants.");
                StageFile(src, Path.Combine(StageDir, ArchiveName(ep)));
            }
            Process.Start(new ProcessStartInfo
            {
                FileName = stagedExe,
                Arguments = (install ? "--install " : "--restore ") + "\"" + game + "\"",
                UseShellExecute = true,
                Verb = "runas",
            });
            return true;
        }
        catch (Win32Exception)
        {
            return false;
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return false;
        }
    }

    static string StageDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SamMaxVoixFR");

    static void StageFile(string src, string dst)
    {
        if (File.Exists(dst)) File.Delete(dst);
        if (!CreateHardLink(dst, src, IntPtr.Zero))
            File.Copy(src, dst, true);
    }

    static void CleanupStage()
    {
        string stagedExe = Path.Combine(StageDir, "SamMaxVoixFR.exe");
        if (!File.Exists(stagedExe)) return;
        if (!string.Equals(Path.GetFullPath(Environment.ProcessPath), Path.GetFullPath(stagedExe), StringComparison.OrdinalIgnoreCase))
            return;
        Process.Start(new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = "/c timeout /t 2 /nobreak > nul & rmdir /s /q \"" + StageDir + "\"",
            CreateNoWindow = true,
            UseShellExecute = false,
        });
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CreateHardLink(string fileName, string existingFileName, IntPtr security);

    void Install(string game)
    {
        var missing = Episodes.Where(ep => Payload(ep) == null).ToList();
        if (missing.Count > 0)
            throw new InvalidOperationException("Fichiers de voix manquants pour les épisodes " + string.Join(", ", missing) + ".");
        string archives = Path.Combine(game, "Archives");
        if (!Directory.Exists(archives))
            throw new InvalidOperationException("Le dossier Archives est introuvable dans le jeu.");
        string backup = Path.Combine(archives, "EnglishVoiceBackup");
        Directory.CreateDirectory(backup);
        int step = 0;
        foreach (int ep in Episodes)
        {
            string name = ArchiveName(ep);
            string dst = Path.Combine(archives, name);
            string bak = Path.Combine(backup, name);
            string src = Payload(ep);
            if (!File.Exists(dst))
                throw new InvalidOperationException(name + " est absent du jeu.");
            if (!File.Exists(bak))
            {
                if (new FileInfo(dst).Length > EnglishMaxBytes)
                    throw new InvalidOperationException(
                        name + " est déjà modifié et la voix anglaise n'a pas été sauvegardée. Dans Steam, vérifie l'intégrité des fichiers du jeu, puis relance l'installeur.");
                Write("Sauvegarde de l'épisode " + EpisodeLabel(ep) + "…");
                CopyFile(dst, bak, step, Episodes.Length);
            }
            else if (new FileInfo(bak).Length > EnglishMaxBytes)
            {
                throw new InvalidOperationException("La sauvegarde de l'épisode " + EpisodeLabel(ep) + " n'est pas la version anglaise.");
            }
            Write("Installation de l'épisode " + EpisodeLabel(ep) + "…");
            CopyFile(src, dst, step, Episodes.Length);
            step++;
        }
        Report(1000);
        Write("Voix françaises installées. Dans le jeu, choisis la langue Français.");
    }

    void Restore(string game)
    {
        string archives = Path.Combine(game, "Archives");
        string backup = Path.Combine(archives, "EnglishVoiceBackup");
        int step = 0;
        foreach (int ep in Episodes)
        {
            string name = ArchiveName(ep);
            string bak = Path.Combine(backup, name);
            string dst = Path.Combine(archives, name);
            if (!File.Exists(bak))
                throw new InvalidOperationException("Pas de sauvegarde anglaise pour l'épisode " + EpisodeLabel(ep) + ".");
            if (new FileInfo(bak).Length > EnglishMaxBytes)
                throw new InvalidOperationException("La sauvegarde de l'épisode " + EpisodeLabel(ep) + " n'est pas la version anglaise.");
            Write("Restauration de l'épisode " + EpisodeLabel(ep) + "…");
            CopyFile(bak, dst, step, Episodes.Length);
            step++;
        }
        Report(1000);
        Write("Voix anglaises d'origine restaurées.");
    }

    void CopyFile(string src, string dst, int step, int steps)
    {
        var info = new FileInfo(src);
        string temp = dst + ".tmp";
        byte[] buffer = new byte[1024 * 1024];
        using (var input = info.OpenRead())
        using (var output = File.Create(temp))
        {
            long done = 0;
            int read;
            while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
            {
                output.Write(buffer, 0, read);
                done += read;
                double filePart = info.Length == 0 ? 1 : (double)done / info.Length;
                Report((int)((step + filePart) / steps * 1000));
            }
        }
        if (File.Exists(dst)) File.SetAttributes(dst, FileAttributes.Normal);
        File.Move(temp, dst, overwrite: true);
    }

    static string ArchiveName(int episode) => $"SM1_pc_SamMax{episode}_uncompressed.ttarch2";
    static string EpisodeLabel(int episode) => (episode - 100).ToString();

    static string Payload(int episode)
    {
        string name = ArchiveName(episode);
        foreach (string dir in PayloadDirs())
        {
            string path = Path.Combine(dir, name);
            if (File.Exists(path)) return path;
        }
        return null;
    }

    static bool PayloadReady() => Episodes.All(ep => Payload(ep) != null);

    static IEnumerable<string> PayloadDirs()
    {
        string app = Path.GetDirectoryName(Environment.ProcessPath) ?? AppContext.BaseDirectory;
        yield return app;
        yield return Path.Combine(app, "voix");
        var dir = new DirectoryInfo(app);
        for (int i = 0; i < 6 && dir != null; i++, dir = dir.Parent)
        {
            string patched = Path.Combine(dir.FullName, "patched");
            if (Directory.Exists(patched)) yield return patched;
        }
    }

    static string FindGame()
    {
        var libraries = new List<string>();
        string standard = @"C:\Program Files (x86)\Steam\steamapps";
        if (Directory.Exists(standard)) libraries.Add(standard);
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
            string steam = key?.GetValue("SteamPath") as string;
            if (!string.IsNullOrWhiteSpace(steam))
                libraries.Add(Path.Combine(steam, "steamapps"));
        }
        catch (Exception) { }
        foreach (string library in libraries.ToList())
        {
            string vdf = Path.Combine(library, "libraryfolders.vdf");
            if (!File.Exists(vdf)) continue;
            foreach (Match match in Regex.Matches(File.ReadAllText(vdf), "\"path\"\\s+\"([^\"]+)\""))
                libraries.Add(Path.Combine(match.Groups[1].Value.Replace(@"\\", @"\"), "steamapps"));
        }
        foreach (string library in libraries.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            string game = Path.Combine(library, "common", "Sam & Max Save the World");
            if (File.Exists(Path.Combine(game, "SamMax100.exe"))) return game;
        }
        return "";
    }

    void SetBusy(bool busy)
    {
        _browse.Enabled = !busy;
        _path.Enabled = !busy;
        _install.Enabled = !busy && IsGameDir(_path.Text) && PayloadReady();
        _restore.Enabled = !busy && IsGameDir(_path.Text);
        if (!busy) _bar.Value = 0;
    }

    void Report(int value)
    {
        if (InvokeRequired) { BeginInvoke(() => Report(value)); return; }
        _bar.Value = Math.Clamp(value, 0, 1000);
    }

    void Write(string line)
    {
        if (InvokeRequired) { BeginInvoke(() => Write(line)); return; }
        _log.AppendText(line + Environment.NewLine);
    }
}
