// Instalatorul Studio Harness 1.0: pluginul Roblox Studio + pluginul Claude Code + configurația Codex, într-un singur executabil.
// Se compilează cu csc.exe din .NET Framework (scripts/build-installer.ps1), fără dependențe externe și fără runtime de instalat.
// Nu scrie și nu afișează niciodată tokenuri: codul local rămâne treaba lui install-studio-plugin.ps1.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;

static class Setup
{
    const string Version = "1.0.0";
    const string DefaultManifest = "https://raw.githubusercontent.com/Ombra-Studios/roblox-studio-harness/main/releases/manifest.json";
    const string MarketplaceName = "studio-harness";
    const string PluginName = "roblox-studio-harness";
    const long MaxPackageBytes = 64L * 1024 * 1024;

    static bool dryRun, assumeYes, skipStudio, skipClaude, skipCodex;
    static string manifestUrl = DefaultManifest, sourceOverride;
    static readonly List<string> Done = new List<string>();
    static readonly List<string> Skipped = new List<string>();
    static readonly List<string> Failed = new List<string>();

    static int Main(string[] rawArguments)
    {
        try { Console.OutputEncoding = Encoding.UTF8; } catch { }
        int early = ParseArguments(rawArguments);
        if (early >= 0) return early;

        Title("Studio Harness " + Version + " — instalare");
        if (dryRun) Console.WriteLine("Mod de probă: nu se schimbă nimic pe disc.\n");

        try
        {
            string python = FindPython();
            string source = ResolveSource();
            Console.WriteLine("Sursa pachetului : " + source);
            Console.WriteLine("Python           : " + (python ?? "negăsit"));
            Console.WriteLine();

            if (python == null)
            {
                Fail("Python 3.10 sau mai nou este obligatoriu: daemon-ul și puntea către CLI-uri sunt scrise în Python.");
                Console.WriteLine("Instalează-l de la https://www.python.org/downloads/ (bifează „Add python.exe to PATH”), apoi rulează din nou.");
                return Finish(1);
            }

            if (skipStudio) Skip("Pluginul Roblox Studio (cerut explicit)");
            else InstallStudioPlugin(python, source);

            if (skipClaude) Skip("Pluginul Claude Code (cerut explicit)");
            else InstallClaudePlugin(source);

            if (skipCodex) Skip("Configurația Codex (cerută explicit)");
            else InstallCodexConfig(source);

            return Finish(Failed.Count == 0 ? 0 : 1);
        }
        catch (Exception error)
        {
            Fail(error.Message);
            return Finish(1);
        }
    }

    // ---------- pașii ----------

    static void InstallStudioPlugin(string python, string source)
    {
        Title("1. Pluginul pentru Roblox Studio");
        string robloxDir = Path.Combine(Local(), "Roblox");
        if (!Directory.Exists(robloxDir))
        {
            Skip("Pluginul Roblox Studio: nu am găsit " + robloxDir + " — instalează Roblox Studio, apoi rulează din nou.");
            return;
        }
        string builder = Path.Combine(source, "scripts", "build_studio_plugin.py");
        string packaged = Path.Combine(source, "dist", "StudioHarness.rbxmx");
        if (File.Exists(builder) && Directory.Exists(Path.Combine(source, "studio-plugin")))
        {
            Console.WriteLine("Construiesc pluginul din surse...");
            if (!Run(python, Quote(builder), source, "construirea pluginului")) { Failed.Add("Pluginul Roblox Studio"); return; }
        }
        else if (!File.Exists(packaged))
        {
            Failed.Add("Pluginul Roblox Studio: lipsesc și sursele, și dist\\StudioHarness.rbxmx");
            return;
        }
        string script = Path.Combine(source, "scripts", "install-studio-plugin.ps1");
        if (!File.Exists(script)) { Failed.Add("Pluginul Roblox Studio: lipsește " + script); return; }
        if (!Run("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File " + Quote(script), source, "instalarea pluginului"))
        {
            Failed.Add("Pluginul Roblox Studio");
            return;
        }
        Done.Add("Pluginul Roblox Studio (repornește Studio o dată)");
    }

    static void InstallClaudePlugin(string source)
    {
        Title("2. Pluginul pentru Claude Code");
        string claude = Which("claude.cmd") ?? Which("claude.exe") ?? Which("claude");
        if (claude == null)
        {
            Skip("Pluginul Claude Code: comanda „claude” nu este în PATH (instalează Claude Code, apoi rulează din nou).");
            return;
        }
        if (!File.Exists(Path.Combine(source, ".claude-plugin", "marketplace.json")))
        {
            Skip("Pluginul Claude Code: pachetul nu conține .claude-plugin\\marketplace.json.");
            return;
        }
        if (!Run(claude, "plugin marketplace add " + Quote(source), source, "adăugarea magazinului local")) { Failed.Add("Pluginul Claude Code"); return; }
        if (!Run(claude, "plugin install " + PluginName + "@" + MarketplaceName, source, "instalarea pluginului")) { Failed.Add("Pluginul Claude Code"); return; }
        Done.Add("Pluginul Claude Code (deschide un terminal nou)");
    }

    static void InstallCodexConfig(string source)
    {
        Title("3. Configurația pentru Codex");
        string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        bool hasCodex = Which("codex.cmd") != null || Which("codex.exe") != null || Which("codex") != null
                        || Directory.Exists(Path.Combine(home, ".codex"));
        if (!hasCodex)
        {
            Skip("Configurația Codex: nu am găsit nici comanda „codex”, nici folderul .codex.");
            return;
        }
        string script = Path.Combine(source, "scripts", "install-codex-config.ps1");
        if (!File.Exists(script)) { Skip("Configurația Codex: lipsește " + script); return; }
        if (!Run("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File " + Quote(script), source, "configurarea Codex"))
        {
            Failed.Add("Configurația Codex");
            return;
        }
        Done.Add("Configurația Codex (repornește Codex)");
    }

    // ---------- sursa pachetului ----------

    static string ResolveSource()
    {
        if (sourceOverride != null)
        {
            string chosen = Path.GetFullPath(sourceOverride);
            if (!LooksLikePackage(chosen)) throw new Exception("În " + chosen + " nu găsesc un pachet Studio Harness (scripts\\install-studio-plugin.ps1).");
            return chosen;
        }
        string here = Path.GetDirectoryName(ExecutablePath());
        for (string folder = here; folder != null; folder = Path.GetDirectoryName(folder.TrimEnd('\\')))
        {
            if (LooksLikePackage(folder)) return folder;
            string inner = Path.Combine(folder, PluginName);
            if (LooksLikePackage(inner)) return inner;
        }
        return Download();
    }

    static bool LooksLikePackage(string folder)
    {
        return folder != null && File.Exists(Path.Combine(folder, "scripts", "install-studio-plugin.ps1"));
    }

    static string Download()
    {
        Console.WriteLine("Nu am găsit pachetul lângă acest fișier: îl descarc.");
        Console.WriteLine("Manifest: " + manifestUrl);
        if (dryRun) { Console.WriteLine("(probă: nu descarc nimic)\n"); return "<pachet descărcat>"; }

        ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072; // TLS 1.2
        string manifest = Encoding.UTF8.GetString(Fetch(manifestUrl, 1024 * 1024));
        string url = Field(manifest, "plugin", "url"), sha = Field(manifest, "plugin", "sha256");
        if (url == null || sha == null) throw new Exception("Manifestul nu conține pachetul „plugin” cu url și sha256.");
        if (!url.StartsWith("https://", StringComparison.OrdinalIgnoreCase)) throw new Exception("Pachetul nu este servit prin HTTPS: " + url);

        Console.WriteLine("Descarc " + url);
        byte[] data = Fetch(url, MaxPackageBytes);
        string actual = Hash(data);
        if (!string.Equals(actual, sha, StringComparison.OrdinalIgnoreCase))
            throw new Exception("Suma de control nu se potrivește: pachetul descărcat nu este cel din manifest.");
        Console.WriteLine("Sumă de control verificată (" + actual.Substring(0, 16) + "…), " + (data.Length / 1024) + " KB.");

        string target = Path.Combine(Local(), "StudioHarness", "pachet");
        if (Directory.Exists(target)) Directory.Delete(target, true);
        Directory.CreateDirectory(target);
        Extract(data, target);
        string root = target;
        string[] entries = Directory.GetDirectories(target);
        if (entries.Length == 1 && Directory.GetFiles(target).Length == 0) root = entries[0];
        if (!LooksLikePackage(root)) throw new Exception("Arhiva descărcată nu are forma așteptată.");
        Console.WriteLine("Despachetat în " + root + "\n");
        return root;
    }

    static byte[] Fetch(string url, long limit)
    {
        using (WebClient client = new WebClient())
        {
            client.Headers.Add("User-Agent", "StudioHarnessSetup/" + Version);
            byte[] data = client.DownloadData(url);
            if (data.LongLength > limit) throw new Exception("Fișierul descărcat depășește limita admisă.");
            return data;
        }
    }

    static void Extract(byte[] archive, string target)
    {
        string full = Path.GetFullPath(target).TrimEnd('\\') + "\\";
        using (MemoryStream stream = new MemoryStream(archive))
        using (ZipArchive zip = new ZipArchive(stream, ZipArchiveMode.Read))
        {
            foreach (ZipArchiveEntry entry in zip.Entries)
            {
                string destination = Path.GetFullPath(Path.Combine(target, entry.FullName.Replace('/', '\\')));
                if (!destination.StartsWith(full, StringComparison.OrdinalIgnoreCase))
                    throw new Exception("Arhiva conține o cale în afara folderului de instalare: " + entry.FullName);
                if (entry.FullName.EndsWith("/") || entry.Name.Length == 0) { Directory.CreateDirectory(destination); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                entry.ExtractToFile(destination, true);
            }
        }
    }

    // Citește "url"/"sha256" din obiectul `files.<name>` al manifestului, fără un parser JSON complet.
    static string Field(string json, string package, string key)
    {
        int at = json.IndexOf("\"" + package + "\"", StringComparison.Ordinal);
        if (at < 0) return null;
        int found = json.IndexOf("\"" + key + "\"", at, StringComparison.Ordinal);
        if (found < 0) return null;
        int start = json.IndexOf('"', json.IndexOf(':', found) + 1);
        int end = start < 0 ? -1 : json.IndexOf('"', start + 1);
        return start < 0 || end < 0 ? null : json.Substring(start + 1, end - start - 1);
    }

    static string Hash(byte[] data)
    {
        using (SHA256 sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "").ToLowerInvariant();
    }

    // ---------- unelte ----------

    static string FindPython()
    {
        foreach (string candidate in new[] { "python.exe", "python3.exe" })
        {
            string found = Which(candidate);
            if (found != null && Run(found, "-c \"import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)\"", null, null, true)) return found;
        }
        string launcher = Which("py.exe");
        if (launcher != null && Run(launcher, "-3 -c \"import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)\"", null, null, true)) return launcher;
        return null;
    }

    static string Which(string name)
    {
        string path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string folder in path.Split(';'))
        {
            if (folder.Length == 0) continue;
            string candidate;
            try { candidate = Path.Combine(folder.Trim('"'), name); } catch { continue; }
            if (File.Exists(candidate)) return candidate;
        }
        return null;
    }

    static bool Run(string program, string arguments, string workingDirectory, string what, bool quiet = false)
    {
        if (dryRun && !quiet)
        {
            Console.WriteLine("  (probă) " + program + " " + arguments);
            return true;
        }
        ProcessStartInfo info = new ProcessStartInfo(program, arguments)
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        if (workingDirectory != null) info.WorkingDirectory = workingDirectory;
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        try
        {
            using (Process process = Process.Start(info))
            {
                string output = process.StandardOutput.ReadToEnd();
                string errors = process.StandardError.ReadToEnd();
                process.WaitForExit();
                if (!quiet)
                {
                    foreach (string line in (output + errors).Split('\n'))
                        if (line.Trim().Length > 0) Console.WriteLine("  " + line.TrimEnd());
                }
                if (process.ExitCode != 0 && !quiet)
                    Console.WriteLine("  Eroare la " + what + " (cod " + process.ExitCode + ").");
                return process.ExitCode == 0;
            }
        }
        catch (Exception error)
        {
            if (!quiet) Console.WriteLine("  Nu am putut porni " + program + ": " + error.Message);
            return false;
        }
    }

    static string Local() { return Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData); }
    static string Quote(string value) { return "\"" + value + "\""; }
    static string ExecutablePath() { return Process.GetCurrentProcess().MainModule.FileName; }

    static void Title(string text)
    {
        Console.WriteLine();
        Console.WriteLine(text);
        Console.WriteLine(new string('-', text.Length));
    }

    static void Skip(string text) { Skipped.Add(text); Console.WriteLine("  Sărit: " + text); }
    static void Fail(string text) { Failed.Add(text); Console.WriteLine("  Eroare: " + text); }

    static int Finish(int code)
    {
        Title("Rezumat");
        foreach (string item in Done) Console.WriteLine("  instalat : " + item);
        foreach (string item in Skipped) Console.WriteLine("  sărit    : " + item);
        foreach (string item in Failed) Console.WriteLine("  eșuat    : " + item);
        if (Done.Count > 0)
        {
            Console.WriteLine();
            Console.WriteLine("Mai departe:");
            Console.WriteLine("  1. Salvează lucrul din Roblox Studio și repornește-l o dată (pentru încărcătorul pluginului).");
            Console.WriteLine("  2. Deschide un terminal nou Claude Code sau Codex: sesiunea apare live în plugin.");
            Console.WriteLine("  3. Cere-i adminului să îți aprobe dispozitivul din panoul echipei.");
        }
        if (!assumeYes)
        {
            Console.WriteLine();
            Console.Write("Apasă Enter ca să închizi.");
            try { Console.ReadLine(); } catch { }
        }
        return code;
    }

    // Întoarce codul de ieșire când programul trebuie oprit acum (0 pentru --help, 2 pentru o opțiune greșită), altfel -1.
    static int ParseArguments(string[] arguments)
    {
        foreach (string argument in arguments)
        {
            string value = argument.Trim();
            if (value == "--dry-run") dryRun = true;
            else if (value == "--yes" || value == "-y") assumeYes = true;
            else if (value == "--skip-studio") skipStudio = true;
            else if (value == "--skip-claude") skipClaude = true;
            else if (value == "--skip-codex") skipCodex = true;
            else if (value.StartsWith("--dir=")) sourceOverride = value.Substring(6).Trim('"');
            else if (value.StartsWith("--manifest=")) manifestUrl = value.Substring(11).Trim('"');
            else if (value == "--help" || value == "-h" || value == "/?")
            {
                Console.WriteLine("Studio Harness " + Version + " — instalare");
                Console.WriteLine();
                Console.WriteLine("  StudioHarnessSetup.exe [opțiuni]");
                Console.WriteLine();
                Console.WriteLine("  --dry-run        arată pașii fără să schimbe nimic");
                Console.WriteLine("  --yes, -y        nu aștepta Enter la final");
                Console.WriteLine("  --dir=<cale>     folosește pachetul din acest folder");
                Console.WriteLine("  --manifest=<url> alt canal de descărcare (implicit cel public)");
                Console.WriteLine("  --skip-studio    nu instala pluginul Roblox Studio");
                Console.WriteLine("  --skip-claude    nu instala pluginul Claude Code");
                Console.WriteLine("  --skip-codex     nu configura Codex");
                return 0;
            }
            else
            {
                Console.WriteLine("Opțiune necunoscută: " + value + " (încearcă --help)");
                return 2;
            }
        }
        return -1;
    }
}
