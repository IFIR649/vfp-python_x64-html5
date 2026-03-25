using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace VfpWebViewHost
{
    public class MainForm : Form
    {
        private static readonly Uri UiUri = new("http://127.0.0.1:8765/ui");
        private static readonly Uri HealthUri = new("http://127.0.0.1:8765/health");
        private static readonly HttpClient HttpClient = new()
        {
            Timeout = TimeSpan.FromSeconds(2),
        };

        private readonly ToolStripButton retryButton;
        private readonly ToolStripButton openBrowserButton;
        private readonly ToolStripLabel statusLabel;
        private readonly WebView2 webView;

        public MainForm()
        {
            Text = "Chat Analitico";
            Width = 1180;
            Height = 820;
            StartPosition = FormStartPosition.CenterScreen;

            var toolStrip = new ToolStrip
            {
                GripStyle = ToolStripGripStyle.Hidden,
                Stretch = true,
                Padding = new Padding(8, 6, 8, 6),
            };

            retryButton = new ToolStripButton("Reintentar");
            retryButton.Click += async (_, _) => await NavigateToBackendAsync();

            openBrowserButton = new ToolStripButton("Abrir en navegador");
            openBrowserButton.Click += (_, _) => OpenBackendInBrowser();

            statusLabel = new ToolStripLabel("Inicializando...");

            toolStrip.Items.Add(retryButton);
            toolStrip.Items.Add(openBrowserButton);
            toolStrip.Items.Add(new ToolStripSeparator());
            toolStrip.Items.Add(statusLabel);

            webView = new WebView2
            {
                Dock = DockStyle.Fill,
            };

            var layout = new ToolStripContainer
            {
                Dock = DockStyle.Fill,
            };
            layout.TopToolStripPanel.Controls.Add(toolStrip);
            layout.ContentPanel.Controls.Add(webView);

            Controls.Add(layout);
            Load += MainForm_Load;
        }

        private async void MainForm_Load(object? sender, EventArgs e)
        {
            await NavigateToBackendAsync();
        }

        private async Task EnsureWebViewAsync()
        {
            if (webView.CoreWebView2 != null)
            {
                return;
            }

            string userDataFolder = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "VfpWebViewHost",
                "WebView2"
            );

            var environment = await CoreWebView2Environment.CreateAsync(null, userDataFolder);
            await webView.EnsureCoreWebView2Async(environment);

            var coreWebView = webView.CoreWebView2
                ?? throw new InvalidOperationException("WebView2 no se pudo inicializar.");

            coreWebView.Settings.IsStatusBarEnabled = false;
            coreWebView.NavigationCompleted += CoreWebView2_NavigationCompleted;
        }

        private async Task NavigateToBackendAsync()
        {
            retryButton.Enabled = false;
            openBrowserButton.Enabled = false;
            SetStatus("Inicializando WebView2...");

            try
            {
                await EnsureWebViewAsync();

                SetStatus("Esperando backend local...");
                bool backendReady = await WaitForBackendAsync();
                if (!backendReady)
                {
                    ShowInlineMessage(
                        "Backend local no disponible",
                        "Inicia FastAPI en http://127.0.0.1:8765 y vuelve a intentar."
                    );
                    SetStatus("Backend no disponible");
                    return;
                }

                SetStatus("Cargando UI...");
                openBrowserButton.Enabled = true;
                webView.CoreWebView2.Navigate(UiUri.ToString());
            }
            catch (Exception ex)
            {
                SetStatus("Error al iniciar WebView2");

                if (webView.CoreWebView2 != null)
                {
                    ShowInlineMessage("No se pudo iniciar WebView2", ex.Message);
                }
                else
                {
                    MessageBox.Show(
                        this,
                        $"No se pudo iniciar WebView2.{Environment.NewLine}{Environment.NewLine}{ex.Message}",
                        "VfpWebViewHost",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                }
            }
            finally
            {
                retryButton.Enabled = true;
            }
        }

        private static async Task<bool> WaitForBackendAsync()
        {
            for (int attempt = 0; attempt < 10; attempt++)
            {
                try
                {
                    using var response = await HttpClient.GetAsync(HealthUri);
                    if (response.IsSuccessStatusCode)
                    {
                        return true;
                    }
                }
                catch (HttpRequestException)
                {
                }
                catch (TaskCanceledException)
                {
                }

                await Task.Delay(1000);
            }

            return false;
        }

        private void CoreWebView2_NavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            SetStatus(e.IsSuccess ? "Conectado a localhost" : "Error de navegacion");
        }

        private void OpenBackendInBrowser()
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = UiUri.ToString(),
                UseShellExecute = true,
            });
        }

        private void ShowInlineMessage(string title, string detail)
        {
            string encodedTitle = WebUtility.HtmlEncode(title);
            string encodedDetail = WebUtility.HtmlEncode(detail);

            string html = $$$"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>VfpWebViewHost</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: linear-gradient(160deg, #082f49, #020617 58%);
      color: #e2e8f0;
      font-family: "Segoe UI", sans-serif;
    }}

    .card {{
      width: min(640px, calc(100vw - 48px));
      padding: 28px;
      border-radius: 20px;
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid rgba(148, 163, 184, 0.2);
      box-shadow: 0 24px 60px rgba(2, 6, 23, 0.4);
    }}

    h1 {{
      margin: 0 0 12px;
      font-size: 28px;
      letter-spacing: -0.04em;
    }}

    p {{
      margin: 0 0 12px;
      line-height: 1.6;
      color: #cbd5e1;
    }}

    code {{
      color: #7dd3fc;
      font-family: Consolas, monospace;
    }}
  </style>
</head>
<body>
  <section class="card">
    <h1>{{{encodedTitle}}}</h1>
    <p>{{{encodedDetail}}}</p>
    <p>Prueba iniciar el backend con <code>scripts\start_backend.bat</code> o <code>scripts\start_demo.ps1</code> y luego pulsa <code>Reintentar</code>.</p>
  </section>
</body>
</html>
""";

            webView.CoreWebView2.NavigateToString(html);
        }

        private void SetStatus(string message)
        {
            statusLabel.Text = message;
        }
    }
}
