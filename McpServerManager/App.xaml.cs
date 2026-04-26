namespace McpServerManager;

public partial class App : System.Windows.Application
{
    protected override void OnStartup(System.Windows.StartupEventArgs e)
    {
        base.OnStartup(e);
        AppLogger.Info("McpServerManager starting.");

        DispatcherUnhandledException += (_, args) =>
        {
            AppLogger.Error("Dispatcher unhandled exception.", args.Exception);
            args.Handled = true;
            System.Windows.MessageBox.Show(
                args.Exception.Message,
                "MCP Server Manager Error",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        };

        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            AppLogger.Error("AppDomain unhandled exception.", args.ExceptionObject as Exception);
        };

        TaskScheduler.UnobservedTaskException += (_, args) =>
        {
            AppLogger.Error("Unobserved task exception.", args.Exception);
            args.SetObserved();
        };
    }

    protected override void OnExit(System.Windows.ExitEventArgs e)
    {
        AppLogger.Info($"McpServerManager exiting with code {e.ApplicationExitCode}.");
        base.OnExit(e);
    }
}
