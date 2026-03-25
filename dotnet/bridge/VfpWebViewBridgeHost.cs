using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using System.Drawing;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

[assembly: ComVisible(false)]

namespace VfpWebViewBridge;

[ComVisible(true)]
[Guid("fac857e5-1aaa-4904-97a7-c89c925e342e")]
[InterfaceType(ComInterfaceType.InterfaceIsDual)]
public interface IVfpWebViewBridgeHost
{
    [DispId(1)]
    bool Attach(int parentHwnd, int left, int top, int width, int height, string url);

    [DispId(2)]
    bool Resize(int left, int top, int width, int height);

    [DispId(3)]
    bool Navigate(string url);

    [DispId(4)]
    void SetVisible(bool visible);

    [DispId(5)]
    void Destroy();

    [DispId(6)]
    string LastError { get; }
}

[ComVisible(true)]
[Guid("b24278cc-7ede-4f28-b76f-215c0e98caf3")]
[ProgId("VfpWebViewBridge.Host")]
[ClassInterface(ClassInterfaceType.None)]
[ComDefaultInterface(typeof(IVfpWebViewBridgeHost))]
public sealed class VfpWebViewBridgeHost : IVfpWebViewBridgeHost
{
    private readonly object syncRoot = new();
    private HostApartment? apartment;
    private string lastError = string.Empty;

    public string LastError => lastError;

    public bool Attach(int parentHwnd, int left, int top, int width, int height, string url)
    {
        try
        {
            var bounds = NormalizeBounds(left, top, width, height);
            var host = EnsureApartment();
            bool attached = host.InvokeAsync(form => form.AttachAsync(new IntPtr(parentHwnd), bounds, url ?? string.Empty));
            SetError(string.Empty);
            return attached;
        }
        catch (Exception ex)
        {
            SetError(ex);
            return false;
        }
    }

    public bool Resize(int left, int top, int width, int height)
    {
        try
        {
            if (apartment is null)
            {
                return false;
            }

            var bounds = NormalizeBounds(left, top, width, height);
            bool resized = apartment.Invoke(form => form.UpdateBounds(bounds));
            SetError(string.Empty);
            return resized;
        }
        catch (Exception ex)
        {
            SetError(ex);
            return false;
        }
    }

    public bool Navigate(string url)
    {
        try
        {
            if (apartment is null)
            {
                SetError("El host WebView2 no esta inicializado.");
                return false;
            }

            bool navigated = apartment.InvokeAsync(form => form.NavigateAsync(url ?? string.Empty));
            SetError(string.Empty);
            return navigated;
        }
        catch (Exception ex)
        {
            SetError(ex);
            return false;
        }
    }

    public void SetVisible(bool visible)
    {
        try
        {
            apartment?.Invoke(form =>
            {
                form.SetHostVisible(visible);
                return true;
            });
        }
        catch (Exception ex)
        {
            SetError(ex);
        }
    }

    public void Destroy()
    {
        lock (syncRoot)
        {
            apartment?.Dispose();
            apartment = null;
        }
    }

    private HostApartment EnsureApartment()
    {
        lock (syncRoot)
        {
            apartment ??= new HostApartment();
            return apartment;
        }
    }

    private static Rectangle NormalizeBounds(int left, int top, int width, int height)
    {
        int normalizedWidth = Math.Max(32, width);
        int normalizedHeight = Math.Max(32, height);
        return new Rectangle(left, top, normalizedWidth, normalizedHeight);
    }

    private void SetError(Exception ex)
    {
        SetError(ex.Message);
    }

    private void SetError(string message)
    {
        lastError = message;
    }
}

internal sealed class HostApartment : IDisposable
{
    private readonly ManualResetEventSlim ready = new(false);
    private readonly Thread thread;
    private HostContext? context;
    private bool disposed;

    public HostApartment()
    {
        thread = new Thread(ThreadMain)
        {
            IsBackground = true,
            Name = "VfpWebViewBridge.UI"
        };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();

        if (!ready.Wait(TimeSpan.FromSeconds(10)) || context is null)
        {
            throw new InvalidOperationException("No se pudo iniciar el hilo STA para WebView2.");
        }
    }

    public T Invoke<T>(Func<EmbeddedHostForm, T> action)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        return context!.Invoke(action);
    }

    public T InvokeAsync<T>(Func<EmbeddedHostForm, Task<T>> action)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        return context!.InvokeAsync(action);
    }

    public void Dispose()
    {
        if (disposed)
        {
            return;
        }

        disposed = true;

        if (context is not null)
        {
            context.DisposeContext();
        }

        if (!thread.Join(TimeSpan.FromSeconds(5)))
        {
            throw new InvalidOperationException("No se pudo cerrar el hilo STA del bridge WebView2.");
        }
    }

    private void ThreadMain()
    {
        Application.EnableVisualStyles();
        context = new HostContext(ready);
        Application.Run(context);
    }
}

internal sealed class HostContext : ApplicationContext
{
    private readonly Control dispatcher;
    private readonly ManualResetEventSlim readySignal;

    public HostContext(ManualResetEventSlim readySignal)
    {
        this.readySignal = readySignal;
        dispatcher = new Control();
        dispatcher.CreateControl();
        Form = new EmbeddedHostForm();
        this.readySignal.Set();
    }

    public EmbeddedHostForm Form { get; }

    public T Invoke<T>(Func<EmbeddedHostForm, T> action)
    {
        var completion = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        Post(() =>
        {
            try
            {
                completion.SetResult(action(Form));
            }
            catch (Exception ex)
            {
                completion.SetException(ex);
            }
        });

        return completion.Task.GetAwaiter().GetResult();
    }

    public T InvokeAsync<T>(Func<EmbeddedHostForm, Task<T>> action)
    {
        var completion = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        Post(async () =>
        {
            try
            {
                completion.SetResult(await action(Form).ConfigureAwait(true));
            }
            catch (Exception ex)
            {
                completion.SetException(ex);
            }
        });

        return completion.Task.GetAwaiter().GetResult();
    }

    public void DisposeContext()
    {
        var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        Post(() =>
        {
            try
            {
                Form.Dispose();
                dispatcher.Dispose();
                ExitThread();
                completion.SetResult(true);
            }
            catch (Exception ex)
            {
                completion.SetException(ex);
            }
        });

        completion.Task.GetAwaiter().GetResult();
    }

    private void Post(Action action)
    {
        if (!dispatcher.IsHandleCreated || dispatcher.IsDisposed)
        {
            throw new InvalidOperationException("El dispatcher WinForms ya no esta disponible.");
        }

        dispatcher.BeginInvoke(action);
    }
}

internal sealed class EmbeddedHostForm : Form
{
    private readonly WebView2 webView;
    private IntPtr currentParent = IntPtr.Zero;
    private static nint webView2LoaderHandle;

    public EmbeddedHostForm()
    {
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        BackColor = Color.FromArgb(2, 6, 23);

        webView = new WebView2
        {
            Dock = DockStyle.Fill
        };

        Controls.Add(webView);
    }

    public async Task<bool> AttachAsync(IntPtr parentHwnd, Rectangle bounds, string url)
    {
        if (parentHwnd == IntPtr.Zero)
        {
            throw new ArgumentException("El HWND del contenedor VFP es obligatorio.", nameof(parentHwnd));
        }

        EnsureChildWindow(parentHwnd);
        UpdateBounds(bounds);
        await EnsureWebViewAsync().ConfigureAwait(true);

        if (!string.IsNullOrWhiteSpace(url))
        {
            webView.CoreWebView2!.Navigate(url);
        }

        return true;
    }

    public bool UpdateBounds(Rectangle bounds)
    {
        if (currentParent == IntPtr.Zero)
        {
            return false;
        }

        NativeMethods.SetWindowPos(
            Handle,
            IntPtr.Zero,
            bounds.Left,
            bounds.Top,
            bounds.Width,
            bounds.Height,
            NativeMethods.SWP_NOZORDER | NativeMethods.SWP_NOACTIVATE | NativeMethods.SWP_SHOWWINDOW | NativeMethods.SWP_FRAMECHANGED
        );

        return true;
    }

    public async Task<bool> NavigateAsync(string url)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            throw new ArgumentException("La URL no puede estar vacia.", nameof(url));
        }

        await EnsureWebViewAsync().ConfigureAwait(true);
        webView.CoreWebView2!.Navigate(url);
        return true;
    }

    public void SetHostVisible(bool visible)
    {
        if (visible)
        {
            Show();
            NativeMethods.ShowWindow(Handle, NativeMethods.SW_SHOW);
            return;
        }

        NativeMethods.ShowWindow(Handle, NativeMethods.SW_HIDE);
        Hide();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            webView.Dispose();
        }

        base.Dispose(disposing);
    }

    private async Task EnsureWebViewAsync()
    {
        if (webView.CoreWebView2 is not null)
        {
            return;
        }

        EnsureWebViewLoaderAvailable();

        string userDataFolder = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "VfpWebViewBridge",
            "WebView2"
        );

        var environment = await CoreWebView2Environment.CreateAsync(null, userDataFolder).ConfigureAwait(true);
        await webView.EnsureCoreWebView2Async(environment).ConfigureAwait(true);

        var core = webView.CoreWebView2
            ?? throw new InvalidOperationException("WebView2 no se pudo inicializar.");

        core.Settings.IsStatusBarEnabled = false;
        core.NavigationCompleted += (_, args) =>
        {
            if (!args.IsSuccess)
            {
                ShowInlineMessage("Error de navegacion", "No se pudo cargar la UI local dentro del formulario VFP.");
            }
        };
    }

    private static void EnsureWebViewLoaderAvailable()
    {
        if (webView2LoaderHandle != 0)
        {
            return;
        }

        lock (typeof(EmbeddedHostForm))
        {
            if (webView2LoaderHandle != 0)
            {
                return;
            }

            string assemblyDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)
                ?? AppContext.BaseDirectory;

            string[] candidates =
            [
                Path.Combine(assemblyDir, "WebView2Loader.dll"),
                Path.Combine(assemblyDir, "runtimes", "win-x86", "native", "WebView2Loader.dll"),
                Path.Combine(AppContext.BaseDirectory, "WebView2Loader.dll"),
                Path.Combine(AppContext.BaseDirectory, "runtimes", "win-x86", "native", "WebView2Loader.dll")
            ];

            foreach (string candidate in candidates)
            {
                if (!File.Exists(candidate))
                {
                    continue;
                }

                try
                {
                    webView2LoaderHandle = NativeLibrary.Load(candidate);
                    return;
                }
                catch
                {
                }
            }

            throw new DllNotFoundException(
                "No se pudo cargar WebView2Loader.dll desde el directorio del bridge COM. " +
                "Verifica que exista junto al ensamblado o en runtimes\\win-x86\\native."
            );
        }
    }

    private void EnsureChildWindow(IntPtr parentHwnd)
    {
        currentParent = parentHwnd;
        _ = Handle;

        int style = NativeMethods.GetWindowLong(Handle, NativeMethods.GWL_STYLE);
        style &= ~NativeMethods.WS_POPUP;
        style &= ~NativeMethods.WS_CAPTION;
        style &= ~NativeMethods.WS_THICKFRAME;
        style |= NativeMethods.WS_CHILD | NativeMethods.WS_VISIBLE | NativeMethods.WS_CLIPCHILDREN | NativeMethods.WS_CLIPSIBLINGS;

        NativeMethods.SetWindowLong(Handle, NativeMethods.GWL_STYLE, style);
        NativeMethods.SetParent(Handle, parentHwnd);
        NativeMethods.ShowWindow(Handle, NativeMethods.SW_SHOW);
    }

    private void ShowInlineMessage(string title, string detail)
    {
        if (webView.CoreWebView2 is null)
        {
            return;
        }

        string encodedTitle = WebUtility.HtmlEncode(title);
        string encodedDetail = WebUtility.HtmlEncode(detail);
        string html = $@"
<!doctype html>
<html lang=""es"">
<head>
  <meta charset=""utf-8"">
  <title>VFP WebView Bridge</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: linear-gradient(160deg, #082f49, #020617 58%);
      color: #e2e8f0;
      font-family: ""Segoe UI"", sans-serif;
    }}

    .card {{
      width: min(560px, calc(100vw - 48px));
      padding: 24px;
      border-radius: 18px;
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid rgba(148, 163, 184, 0.22);
      box-shadow: 0 24px 60px rgba(2, 6, 23, 0.4);
    }}

    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
    }}

    p {{
      margin: 0;
      line-height: 1.6;
      color: #cbd5e1;
    }}
  </style>
</head>
<body>
  <section class=""card"">
    <h1>{encodedTitle}</h1>
    <p>{encodedDetail}</p>
  </section>
</body>
</html>
";

        webView.CoreWebView2.NavigateToString(html);
    }
}

internal static class NativeMethods
{
    public const int GWL_STYLE = -16;
    public const int WS_CHILD = 0x40000000;
    public const int WS_VISIBLE = 0x10000000;
    public const int WS_POPUP = unchecked((int)0x80000000);
    public const int WS_CAPTION = 0x00C00000;
    public const int WS_THICKFRAME = 0x00040000;
    public const int WS_CLIPSIBLINGS = 0x04000000;
    public const int WS_CLIPCHILDREN = 0x02000000;
    public const uint SWP_NOZORDER = 0x0004;
    public const uint SWP_NOACTIVATE = 0x0010;
    public const uint SWP_SHOWWINDOW = 0x0040;
    public const uint SWP_FRAMECHANGED = 0x0020;
    public const int SW_SHOW = 5;
    public const int SW_HIDE = 0;

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr SetParent(IntPtr hWndChild, IntPtr hWndNewParent);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetWindowPos(
        IntPtr hWnd,
        IntPtr hWndInsertAfter,
        int x,
        int y,
        int cx,
        int cy,
        uint uFlags
    );

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll", EntryPoint = "GetWindowLong", SetLastError = true)]
    public static extern int GetWindowLong(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", EntryPoint = "SetWindowLong", SetLastError = true)]
    public static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
}
