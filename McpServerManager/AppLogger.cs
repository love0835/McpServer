using System.IO;
using System.Text;

namespace McpServerManager;

public static class AppLogger
{
    private static readonly object Sync = new();

    public static string LogPath
    {
        get
        {
            var baseDir = AppContext.BaseDirectory;
            var preferred = Path.Combine(baseDir, "logs");
            if (!Directory.Exists(preferred))
            {
                preferred = @"E:\McpServer\McpServerManager\logs";
            }
            Directory.CreateDirectory(preferred);
            return Path.Combine(preferred, "manager.log");
        }
    }

    public static void Info(string message) => Write("INFO", message);

    public static void Error(string message, Exception? exception = null)
    {
        var text = exception is null ? message : $"{message}{Environment.NewLine}{exception}";
        Write("ERROR", text);
    }

    private static void Write(string level, string message)
    {
        lock (Sync)
        {
            File.AppendAllText(
                LogPath,
                $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff}] [{level}] {message}{Environment.NewLine}",
                Encoding.UTF8);
        }
    }
}
