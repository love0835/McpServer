using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Net.Sockets;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Windows;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

namespace McpServerManager;

public partial class MainWindow : Window
{
    private readonly ManagerViewModel _vm = new();
    private readonly DispatcherTimer _healthTimer = new();
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(5) };
    private Forms.NotifyIcon? _trayIcon;
    private bool _updatingAutostart;
    private bool _updatingLanguage;

    public MainWindow()
    {
        InitializeComponent();
        AppLogger.Info("Main window initialized.");
        DataContext = _vm;
        LoadServers();
        SetupTray();
        SetLanguageSelection(_vm.Settings.Language);
        ApplyLanguage();

        _healthTimer.Interval = TimeSpan.FromSeconds(Math.Max(3, _vm.Settings.HealthIntervalSec));
        _healthTimer.Tick += async (_, _) => await RefreshHealthAsync();
        _healthTimer.Start();

        Loaded += async (_, _) =>
        {
            if (_vm.Settings.AutostartOnLaunch)
            {
                foreach (var server in _vm.Servers.Where(s => s.Manifest.Autostart))
                {
                    await StartServerAsync(server);
                }
            }
            await RefreshHealthAsync();
        };
        Closing += (_, _) => _trayIcon?.Dispose();
    }

    private void LoadServers()
    {
        AppLogger.Info($"Loading server manifests from {_vm.Settings.ServerRoot}.");
        _vm.Servers.Clear();
        var root = new DirectoryInfo(_vm.Settings.ExpandedServerRoot);
        if (!root.Exists)
        {
            root.Create();
        }

        foreach (var manifestFile in root.EnumerateFiles("mcpserver.json", SearchOption.AllDirectories))
        {
            try
            {
                var manifest = JsonSerializer.Deserialize<ServerManifest>(
                    File.ReadAllText(manifestFile.FullName),
                    JsonOptions.Default);
                if (manifest is null)
                {
                    continue;
                }

                var folder = manifestFile.DirectoryName ?? root.FullName;
            _vm.Servers.Add(new ServerState(manifest, manifestFile.FullName, folder)
                {
                    Status = "Stopped",
                    Health = "Unknown"
                });
            }
            catch (Exception ex)
            {
                AppLogger.Error($"Failed to load manifest: {manifestFile.FullName}", ex);
                _vm.Servers.Add(ServerState.Invalid(manifestFile.FullName, ex.Message));
            }
        }
    }

    private async Task StartServerAsync(ServerState server)
    {
        if (server.Process is { HasExited: false })
        {
            return;
        }

        if (await IsHealthyAsync(server))
        {
            server.Status = "External";
            server.Pid = GetListeningPid(server)?.ToString() ?? "";
            return;
        }

        var cwd = ResolvePath(server.ServerFolder, server.Expand(server.Manifest.Cwd ?? "."));
        Directory.CreateDirectory(cwd);

        var startInfo = new ProcessStartInfo
        {
            FileName = server.Expand(server.Manifest.Command),
            WorkingDirectory = cwd,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };

        foreach (var arg in server.Manifest.Args)
        {
            startInfo.ArgumentList.Add(server.Expand(arg));
        }

        foreach (var item in server.Manifest.Env)
        {
            startInfo.Environment[item.Key] = server.Expand(item.Value);
        }

        var stdout = ResolvePath(server.ServerFolder, server.Expand(server.Manifest.Logging?.Stdout ?? "logs\\stdout.log"));
        var stderr = ResolvePath(server.ServerFolder, server.Expand(server.Manifest.Logging?.Stderr ?? "logs\\stderr.log"));
        Directory.CreateDirectory(Path.GetDirectoryName(stdout)!);
        Directory.CreateDirectory(Path.GetDirectoryName(stderr)!);

        var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, e) => AppendLine(stdout, e.Data);
        process.ErrorDataReceived += (_, e) => AppendLine(stderr, e.Data);
        process.Exited += (_, _) =>
        {
            Dispatcher.Invoke(() =>
            {
                if (server.Process == process)
                {
                    server.Status = process.ExitCode == 0 ? "Stopped" : "Crashed";
                    server.Pid = "";
                    server.StartedAt = null;
                }
            });
        };

        try
        {
            if (!process.Start())
            {
                server.Status = "Failed";
                AppLogger.Error($"Failed to start server {server.Manifest.Name}: process.Start returned false.");
                return;
            }
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            server.Process = process;
            server.Pid = process.Id.ToString();
            server.StartedAt = DateTimeOffset.Now;
            server.Status = "Running";
            AppLogger.Info($"Started server {server.Manifest.Name}, PID {process.Id}.");
        }
        catch (Exception ex)
        {
            server.Status = "Failed";
            server.Health = ex.Message;
            AppLogger.Error($"Failed to start server {server.Manifest.Name}.", ex);
        }
    }

    private static void AppendLine(string path, string? line)
    {
        if (line is null)
        {
            return;
        }
        File.AppendAllText(path, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {line}{Environment.NewLine}", Encoding.UTF8);
    }

    private async Task StopServerAsync(ServerState server)
    {
        if (server.Process is null)
        {
            var externalPid = GetListeningPid(server);
            if (externalPid is null)
            {
                server.Status = "Stopped";
                server.Pid = "";
                return;
            }

            try
            {
                var process = Process.GetProcessById(externalPid.Value);
                process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync();
                process.Dispose();
                server.Status = "Stopped";
                server.Pid = "";
                server.StartedAt = null;
                AppLogger.Info($"Stopped external server {server.Manifest.Name}, PID {externalPid.Value}.");
            }
            catch (Exception ex)
            {
                server.Health = ex.Message;
                AppLogger.Error($"Failed to stop external server {server.Manifest.Name}, PID {externalPid.Value}.", ex);
            }
            return;
        }

        try
        {
            if (!server.Process.HasExited)
            {
                server.Process.Kill(entireProcessTree: true);
                await server.Process.WaitForExitAsync();
            }
        }
        catch (Exception ex)
        {
            server.Health = ex.Message;
            AppLogger.Error($"Failed to stop server {server.Manifest.Name}.", ex);
        }
        finally
        {
            server.Process.Dispose();
            server.Process = null;
            server.Pid = "";
            server.StartedAt = null;
            server.Status = "Stopped";
            AppLogger.Info($"Stopped server {server.Manifest.Name}.");
        }
    }

    private async Task RefreshHealthAsync()
    {
        foreach (var server in _vm.Servers)
        {
            if (server.IsInvalid)
            {
                continue;
            }
            var healthy = await IsHealthyAsync(server);
            server.Health = healthy ? "Healthy" : "Down";
            if (server.Process is { HasExited: false })
            {
                server.Status = "Running";
            }
            else if (healthy && server.Status != "Running")
            {
                server.Status = "External";
                server.Pid = GetListeningPid(server)?.ToString() ?? "";
            }
            else if (!healthy && server.Process is null)
            {
                server.Pid = "";
                if (server.Status == "External")
                {
                    server.Status = "Stopped";
                }
            }
            server.RefreshUptime();
        }
        RefreshSelectedLog();
    }

    private async Task<bool> IsHealthyAsync(ServerState server)
    {
        var hc = server.Manifest.Healthcheck;
        if (hc is null || hc.Type == "none")
        {
            return server.Process is { HasExited: false };
        }

        try
        {
            if (hc.Type == "tcp")
            {
                using var client = new TcpClient();
                var host = server.Expand(hc.Host ?? server.Manifest.Host ?? "127.0.0.1");
                var port = hc.Port ?? server.Manifest.Port ?? 0;
                if (port <= 0)
                {
                    return false;
                }
                await client.ConnectAsync(host, port).WaitAsync(TimeSpan.FromSeconds(hc.TimeoutSec ?? 5));
                return client.Connected;
            }

            if (hc.Type == "http")
            {
                var response = await _http.GetAsync(server.Expand(hc.Url ?? "")).WaitAsync(TimeSpan.FromSeconds(hc.TimeoutSec ?? 5));
                return response.IsSuccessStatusCode;
            }

            if (hc.Type == "mcp")
            {
                const string body = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"mcp-server-manager\",\"version\":\"1\"}}}";
                using var req = new HttpRequestMessage(HttpMethod.Post, server.Expand(hc.Url ?? ""));
                req.Headers.TryAddWithoutValidation("Accept", "application/json, text/event-stream");
                req.Content = new StringContent(body, Encoding.UTF8, "application/json");
                var response = await _http.SendAsync(req).WaitAsync(TimeSpan.FromSeconds(hc.TimeoutSec ?? 5));
                return response.IsSuccessStatusCode;
            }
        }
        catch
        {
            return false;
        }

        return false;
    }

    private static int? GetListeningPid(ServerState server)
    {
        var port = server.Manifest.Port;
        if (port is null or <= 0)
        {
            return null;
        }

        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            startInfo.ArgumentList.Add("-NoProfile");
            startInfo.ArgumentList.Add("-NonInteractive");
            startInfo.ArgumentList.Add("-Command");
            startInfo.ArgumentList.Add($"(Get-NetTCPConnection -LocalPort {port.Value} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)");

            using var process = Process.Start(startInfo);
            if (process is null)
            {
                return null;
            }
            var output = process.StandardOutput.ReadToEnd().Trim();
            process.WaitForExit(3000);
            return int.TryParse(output, out var pid) ? pid : null;
        }
        catch
        {
            return null;
        }
    }

    private void RefreshSelectedDetails()
    {
        if (ServerGrid.SelectedItem is not ServerState server)
        {
            SelectedTitle.Text = T("NoServerSelected");
            SelectedDescription.Text = "";
            _updatingAutostart = true;
            AutostartBox.IsEnabled = false;
            AutostartBox.IsChecked = false;
            _updatingAutostart = false;
            LogTailBox.Text = "";
            return;
        }

        SelectedTitle.Text = server.DisplayName;
        SelectedDescription.Text = $"{server.Manifest.Name} | {server.Manifest.Transport} | {server.Manifest.Description}";
        _updatingAutostart = true;
        AutostartBox.IsEnabled = !server.IsInvalid;
        AutostartBox.IsChecked = server.Manifest.Autostart;
        _updatingAutostart = false;
        RefreshSelectedLog();
    }

    private void RefreshSelectedLog()
    {
        if (ServerGrid.SelectedItem is not ServerState server)
        {
            return;
        }

        var stderr = ResolvePath(server.ServerFolder, server.Manifest.Logging?.Stderr ?? "logs\\stderr.log");
        var stdout = ResolvePath(server.ServerFolder, server.Manifest.Logging?.Stdout ?? "logs\\stdout.log");
        var path = File.Exists(stderr) ? stderr : stdout;
        if (!File.Exists(path))
        {
            LogTailBox.Text = $"({T("NoLogYet")})";
            return;
        }

        var lines = File.ReadLines(path).TakeLast(_vm.Settings.LogTailLines);
        LogTailBox.Text = string.Join(Environment.NewLine, lines);
        LogTailBox.ScrollToEnd();
    }

    private void SetupTray()
    {
        _trayIcon = new Forms.NotifyIcon
        {
            Text = "MCP Server Manager",
            Icon = System.Drawing.SystemIcons.Application,
            Visible = true,
            ContextMenuStrip = new Forms.ContextMenuStrip()
        };
        BuildTrayMenu();
        _trayIcon.DoubleClick += (_, _) => ShowFromTray();
    }

    private void BuildTrayMenu()
    {
        if (_trayIcon?.ContextMenuStrip is null)
        {
            return;
        }

        _trayIcon.ContextMenuStrip.Items.Clear();
        _trayIcon.ContextMenuStrip.Items.Add(T("TrayOpen"), null, (_, _) => Dispatcher.Invoke(ShowFromTray));
        _trayIcon.ContextMenuStrip.Items.Add(T("StartAll"), null, async (_, _) => await Dispatcher.InvokeAsync(StartAllAsync));
        _trayIcon.ContextMenuStrip.Items.Add(T("StopAll"), null, async (_, _) => await Dispatcher.InvokeAsync(StopAllAsync));
        _trayIcon.ContextMenuStrip.Items.Add(T("TrayExit"), null, (_, _) => Dispatcher.Invoke(Close));
    }

    private void ShowFromTray()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
    }

    private static string ResolvePath(string baseFolder, string path)
    {
        if (Path.IsPathRooted(path))
        {
            return Path.GetFullPath(path);
        }
        return Path.GetFullPath(Path.Combine(baseFolder, path));
    }

    private async Task StartAllAsync()
    {
        foreach (var server in _vm.Servers.Where(s => !s.IsInvalid))
        {
            await StartServerAsync(server);
        }
        await RefreshHealthAsync();
    }

    private async Task StopAllAsync()
    {
        foreach (var server in _vm.Servers.Where(s => !s.IsInvalid))
        {
            await StopServerAsync(server);
        }
        await RefreshHealthAsync();
    }

    private async void Start_Click(object sender, RoutedEventArgs e)
    {
        if (ServerGrid.SelectedItem is ServerState server)
        {
            await StartServerAsync(server);
            await RefreshHealthAsync();
        }
    }

    private async void Stop_Click(object sender, RoutedEventArgs e)
    {
        if (ServerGrid.SelectedItem is ServerState server)
        {
            await StopServerAsync(server);
            await RefreshHealthAsync();
        }
    }

    private async void Restart_Click(object sender, RoutedEventArgs e)
    {
        if (ServerGrid.SelectedItem is ServerState server)
        {
            await StopServerAsync(server);
            await StartServerAsync(server);
            await RefreshHealthAsync();
        }
    }

    private async void StartAll_Click(object sender, RoutedEventArgs e) => await StartAllAsync();

    private async void StopAll_Click(object sender, RoutedEventArgs e) => await StopAllAsync();

    private async void Reload_Click(object sender, RoutedEventArgs e)
    {
        LoadServers();
        await RefreshHealthAsync();
        AppLogger.Info("Reloaded manifests.");
    }

    private void OpenFolder_Click(object sender, RoutedEventArgs e)
    {
        if (ServerGrid.SelectedItem is ServerState server)
        {
            Process.Start(new ProcessStartInfo("explorer.exe", server.ServerFolder) { UseShellExecute = true });
        }
    }

    private void OpenLogs_Click(object sender, RoutedEventArgs e)
    {
        if (ServerGrid.SelectedItem is ServerState server)
        {
            var logFolder = Path.Combine(server.ServerFolder, "logs");
            Directory.CreateDirectory(logFolder);
            Process.Start(new ProcessStartInfo("explorer.exe", logFolder) { UseShellExecute = true });
        }
    }

    private void RefreshLog_Click(object sender, RoutedEventArgs e) => RefreshSelectedLog();

    private void AutostartBox_Changed(object sender, RoutedEventArgs e)
    {
        if (_updatingAutostart || ServerGrid.SelectedItem is not ServerState server || server.IsInvalid)
        {
            return;
        }

        server.Manifest.Autostart = AutostartBox.IsChecked == true;
        var json = JsonSerializer.Serialize(server.Manifest, JsonOptions.WriteIndented);
        File.WriteAllText(server.ManifestPath, json, Encoding.UTF8);
    }

    private void LanguageBox_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (_updatingLanguage || LanguageBox.SelectedValue is not string language)
        {
            return;
        }

        _vm.Settings.Language = language;
        _vm.Settings.Save();
        ApplyLanguage();
    }

    private void SetLanguageSelection(string language)
    {
        _updatingLanguage = true;
        LanguageBox.SelectedValue = UiText.IsSupported(language) ? language : "zh-TW";
        _updatingLanguage = false;
    }

    private void ApplyLanguage()
    {
        UiText.Language = UiText.IsSupported(_vm.Settings.Language) ? _vm.Settings.Language : "zh-TW";
        Title = T("WindowTitle");
        AppTitleText.Text = T("WindowTitle");
        ServerRootText.Text = $"{T("ServerRoot")}: {_vm.Settings.ExpandedServerRoot}";

        ReloadButton.Content = T("Reload");
        StartAllButton.Content = T("StartAll");
        StopAllButton.Content = T("StopAll");
        StartButton.Content = T("Start");
        StopButton.Content = T("Stop");
        RestartButton.Content = T("Restart");
        OpenFolderButton.Content = T("OpenFolder");
        OpenLogsButton.Content = T("OpenLogs");
        RefreshLogButton.Content = T("RefreshLog");
        AutostartBox.Content = T("Autostart");
        LogTailLabel.Text = T("LogTail");

        NameColumn.Header = T("Name");
        StatusColumn.Header = T("Status");
        HealthColumn.Header = T("Health");
        PidColumn.Header = T("Pid");
        PortColumn.Header = T("Port");
        UptimeColumn.Header = T("Uptime");
        ManifestColumn.Header = T("Manifest");

        foreach (var server in _vm.Servers)
        {
            server.NotifyLocalizedText();
        }

        BuildTrayMenu();
        RefreshSelectedDetails();
    }

    private static string T(string key) => UiText.T(key);

    private void ServerGrid_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        RefreshSelectedDetails();
    }
}

public sealed class ManagerViewModel
{
    public ObservableCollection<ServerState> Servers { get; } = [];
    public ManagerSettings Settings { get; } = ManagerSettings.Load();
}

public sealed class ServerState : INotifyPropertyChanged
{
    private string _status = "";
    private string _health = "";
    private string _pid = "";
    private string _uptimeText = "";

    public ServerState(ServerManifest manifest, string manifestPath, string serverFolder)
    {
        Manifest = manifest;
        ManifestPath = manifestPath;
        ServerFolder = serverFolder;
    }

    public ServerManifest Manifest { get; }
    public string ManifestPath { get; }
    public string ServerFolder { get; }
    public Process? Process { get; set; }
    public DateTimeOffset? StartedAt { get; set; }
    public bool IsInvalid { get; init; }
    public string DisplayName => Manifest.DisplayName;
    public int? Port => Manifest.Port;
    public string StatusDisplay => UiText.TranslateStatus(Status);
    public string HealthDisplay => UiText.TranslateHealth(Health);

    public string Expand(string value) => PathVariables.Expand(value, ServerFolder);

    public string Status
    {
        get => _status;
        set
        {
            SetField(ref _status, value);
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(StatusDisplay)));
        }
    }

    public string Health
    {
        get => _health;
        set
        {
            SetField(ref _health, value);
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HealthDisplay)));
        }
    }

    public string Pid
    {
        get => _pid;
        set => SetField(ref _pid, value);
    }

    public string UptimeText
    {
        get => _uptimeText;
        set => SetField(ref _uptimeText, value);
    }

    public static ServerState Invalid(string manifestPath, string error)
    {
        return new ServerState(
            new ServerManifest
            {
                Name = Path.GetFileName(Path.GetDirectoryName(manifestPath)) ?? "invalid",
                DisplayName = "Invalid manifest",
                Description = error,
                Version = "0.0.0",
                Transport = "unknown",
                Command = ""
            },
            manifestPath,
            Path.GetDirectoryName(manifestPath) ?? ".")
        {
            IsInvalid = true,
            Status = "Invalid",
            Health = error
        };
    }

    public void RefreshUptime()
    {
        if (StartedAt is null)
        {
            UptimeText = "";
            return;
        }
        var delta = DateTimeOffset.Now - StartedAt.Value;
        UptimeText = delta.TotalHours >= 1
            ? $"{(int)delta.TotalHours}h {delta.Minutes}m"
            : $"{delta.Minutes}m {delta.Seconds}s";
    }

    public void NotifyLocalizedText()
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(StatusDisplay)));
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HealthDisplay)));
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return;
        }
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}

public sealed class ManagerSettings
{
    public string ServerRoot { get; set; } = @"${MCP_ROOT}\servers";
    public bool AutostartOnLaunch { get; set; } = true;
    public int HealthIntervalSec { get; set; } = 10;
    public int LogTailLines { get; set; } = 200;
    public string Language { get; set; } = "zh-TW";

    [JsonIgnore]
    public string ExpandedServerRoot => PathVariables.Expand(ServerRoot, null);

    public static ManagerSettings Load()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "config", "settings.json");
        if (!File.Exists(path))
        {
            path = @"E:\McpServer\McpServerManager\config\settings.json";
        }
        if (!File.Exists(path))
        {
            return new ManagerSettings();
        }
        return JsonSerializer.Deserialize<ManagerSettings>(File.ReadAllText(path), JsonOptions.Default)
            ?? new ManagerSettings();
    }

    public void Save()
    {
        var path = SettingsPath();
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, JsonSerializer.Serialize(this, JsonOptions.WriteIndented), Encoding.UTF8);
    }

    private static string SettingsPath()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "config", "settings.json");
        return Directory.Exists(Path.GetDirectoryName(path))
            ? path
            : Path.Combine(PathVariables.ManagerDir, "config", "settings.json");
    }
}

public static class PathVariables
{
    public static string ManagerDir => FindManagerDir();
    public static string McpRoot => Directory.GetParent(ManagerDir)?.FullName ?? AppContext.BaseDirectory;
    public static string ServerRoot => Path.Combine(McpRoot, "servers");

    public static string Expand(string value, string? serverDir)
    {
        var result = value
            .Replace("${MCP_ROOT}", McpRoot, StringComparison.OrdinalIgnoreCase)
            .Replace("${MANAGER_DIR}", ManagerDir, StringComparison.OrdinalIgnoreCase)
            .Replace("${SERVER_ROOT}", ServerRoot, StringComparison.OrdinalIgnoreCase);

        if (serverDir is not null)
        {
            result = result.Replace("${SERVER_DIR}", serverDir, StringComparison.OrdinalIgnoreCase);
        }

        return Environment.ExpandEnvironmentVariables(result);
    }

    private static string FindManagerDir()
    {
        var baseDir = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var current = new DirectoryInfo(baseDir);
        while (current is not null)
        {
            if (string.Equals(current.Name, "McpServerManager", StringComparison.OrdinalIgnoreCase))
            {
                return current.FullName;
            }
            current = current.Parent;
        }
        return baseDir;
    }
}

public sealed class ServerManifest
{
    public string Name { get; set; } = "";
    public string DisplayName { get; set; } = "";
    public string Description { get; set; } = "";
    public string Version { get; set; } = "";
    public string Transport { get; set; } = "";
    public string? Host { get; set; }
    public int? Port { get; set; }
    public string Command { get; set; } = "";
    public List<string> Args { get; set; } = [];
    public string? Cwd { get; set; }
    public Dictionary<string, string> Env { get; set; } = [];
    public HealthCheck? Healthcheck { get; set; }
    public bool Autostart { get; set; }
    public RestartPolicy? RestartPolicy { get; set; }
    public LoggingConfig? Logging { get; set; }
    public Dictionary<string, JsonElement>? Security { get; set; }
    public List<string> Tags { get; set; } = [];
}

public sealed class HealthCheck
{
    public string Type { get; set; } = "none";
    public string? Url { get; set; }
    public string? Host { get; set; }
    public int? Port { get; set; }
    public string? Command { get; set; }
    public List<string> Args { get; set; } = [];
    public int? IntervalSec { get; set; }
    public int? TimeoutSec { get; set; }
}

public sealed class RestartPolicy
{
    public string Mode { get; set; } = "never";
    public int MaxRetries { get; set; }
    public int BackoffSec { get; set; }
}

public sealed class LoggingConfig
{
    public string? Stdout { get; set; }
    public string? Stderr { get; set; }
    public int RotateMB { get; set; } = 10;
}

public static class JsonOptions
{
    public static readonly JsonSerializerOptions Default = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    public static readonly JsonSerializerOptions WriteIndented = new(Default)
    {
        WriteIndented = true
    };
}

public static class UiText
{
    public static string Language { get; set; } = "zh-TW";

    private static readonly Dictionary<string, Dictionary<string, string>> Text = new()
    {
        ["zh-TW"] = new()
        {
            ["WindowTitle"] = "MCP 伺服器管理器",
            ["ServerRoot"] = "伺服器根目錄",
            ["Reload"] = "重新載入",
            ["StartAll"] = "全部啟動",
            ["StopAll"] = "全部停止",
            ["Start"] = "啟動",
            ["Stop"] = "停止",
            ["Restart"] = "重新啟動",
            ["OpenFolder"] = "開啟資料夾",
            ["OpenLogs"] = "開啟日誌",
            ["RefreshLog"] = "重新整理日誌",
            ["Autostart"] = "由 Manager 自動啟動",
            ["LogTail"] = "日誌尾端",
            ["Name"] = "名稱",
            ["Status"] = "狀態",
            ["Health"] = "健康狀態",
            ["Pid"] = "PID",
            ["Port"] = "Port",
            ["Uptime"] = "運行時間",
            ["Manifest"] = "Manifest",
            ["NoServerSelected"] = "未選取伺服器",
            ["NoLogYet"] = "尚無日誌",
            ["TrayOpen"] = "開啟",
            ["TrayExit"] = "結束",
            ["Running"] = "執行中",
            ["Stopped"] = "已停止",
            ["External"] = "外部啟動",
            ["Crashed"] = "已崩潰",
            ["Failed"] = "啟動失敗",
            ["Invalid"] = "Manifest 無效",
            ["Unknown"] = "未知",
            ["Healthy"] = "正常",
            ["Down"] = "離線"
        },
        ["en-US"] = new()
        {
            ["WindowTitle"] = "MCP Server Manager",
            ["ServerRoot"] = "Server root",
            ["Reload"] = "Reload",
            ["StartAll"] = "Start All",
            ["StopAll"] = "Stop All",
            ["Start"] = "Start",
            ["Stop"] = "Stop",
            ["Restart"] = "Restart",
            ["OpenFolder"] = "Open Folder",
            ["OpenLogs"] = "Open Logs",
            ["RefreshLog"] = "Refresh Log",
            ["Autostart"] = "Autostart from Manager",
            ["LogTail"] = "Log Tail",
            ["Name"] = "Name",
            ["Status"] = "Status",
            ["Health"] = "Health",
            ["Pid"] = "PID",
            ["Port"] = "Port",
            ["Uptime"] = "Uptime",
            ["Manifest"] = "Manifest",
            ["NoServerSelected"] = "No server selected",
            ["NoLogYet"] = "No log yet",
            ["TrayOpen"] = "Open",
            ["TrayExit"] = "Exit",
            ["Running"] = "Running",
            ["Stopped"] = "Stopped",
            ["External"] = "External",
            ["Crashed"] = "Crashed",
            ["Failed"] = "Failed",
            ["Invalid"] = "Invalid",
            ["Unknown"] = "Unknown",
            ["Healthy"] = "Healthy",
            ["Down"] = "Down"
        }
    };

    public static bool IsSupported(string language) => Text.ContainsKey(language);

    public static string T(string key)
    {
        if (Text.TryGetValue(Language, out var table) && table.TryGetValue(key, out var value))
        {
            return value;
        }
        return Text["zh-TW"].TryGetValue(key, out var fallback) ? fallback : key;
    }

    public static string TranslateStatus(string status) => T(status);

    public static string TranslateHealth(string health) => T(health);
}
