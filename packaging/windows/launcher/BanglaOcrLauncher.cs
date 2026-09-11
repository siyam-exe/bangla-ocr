using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace BanglaOcr.Windows
{
    internal sealed class HealthResult
    {
        public string State;
        public string Detail;
        public bool ActiveJob;
    }

    internal sealed class LauncherForm : Form
    {
        private const string Host = "127.0.0.1";
        private const int Port = 8765;
        private const string Address = "http://127.0.0.1:8765";

        private readonly Color background = Color.FromArgb(245, 246, 248);
        private readonly Color surface = Color.White;
        private readonly Color text = Color.FromArgb(23, 25, 29);
        private readonly Color muted = Color.FromArgb(98, 105, 117);
        private readonly Color accent = Color.FromArgb(31, 94, 255);
        private readonly Color danger = Color.FromArgb(180, 35, 24);

        private readonly Label statusTitle;
        private readonly Label statusDetail;
        private readonly ProgressBar progress;
        private readonly Button primaryButton;
        private readonly Button secondaryButton;
        private readonly NotifyIcon trayIcon;
        private readonly string applicationRoot;
        private readonly string logPath;

        private Process backend;
        private StreamWriter logWriter;
        private volatile bool starting;
        private volatile bool exiting;
        private bool ready;
        private bool trayMessageShown;

        public LauncherForm()
        {
            applicationRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            logPath = Path.Combine(applicationRoot, "workspace", "logs", "application.log");

            Text = "Bangla OCR";
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(560, 350);
            MinimumSize = new Size(540, 360);
            BackColor = background;
            Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);

            Panel content = new Panel();
            content.BackColor = surface;
            content.BorderStyle = BorderStyle.FixedSingle;
            content.Location = new Point(24, 24);
            content.Size = new Size(512, 302);
            content.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            Controls.Add(content);

            Label brand = CreateLabel("Bangla OCR", 17F, FontStyle.Bold, text);
            brand.Location = new Point(38, 34);
            brand.AutoSize = true;
            content.Controls.Add(brand);

            Label subtitle = CreateLabel("Local document workspace", 9F, FontStyle.Regular, muted);
            subtitle.Location = new Point(40, 72);
            subtitle.AutoSize = true;
            content.Controls.Add(subtitle);

            statusTitle = CreateLabel("Preparing the application", 12F, FontStyle.Bold, text);
            statusTitle.Location = new Point(38, 132);
            statusTitle.AutoSize = true;
            content.Controls.Add(statusTitle);

            statusDetail = CreateLabel("Checking the local service.", 10F, FontStyle.Regular, muted);
            statusDetail.Location = new Point(40, 166);
            statusDetail.Size = new Size(430, 44);
            content.Controls.Add(statusDetail);

            progress = new ProgressBar();
            progress.Location = new Point(40, 211);
            progress.Size = new Size(430, 8);
            progress.Style = ProgressBarStyle.Continuous;
            progress.Value = 8;
            progress.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Bottom;
            content.Controls.Add(progress);

            primaryButton = CreateButton("Open Bangla OCR", true);
            primaryButton.Location = new Point(38, 242);
            primaryButton.Click += OpenButtonClick;
            primaryButton.Visible = false;
            content.Controls.Add(primaryButton);

            secondaryButton = CreateButton("Copy address", false);
            secondaryButton.Location = new Point(200, 242);
            secondaryButton.Click += CopyButtonClick;
            secondaryButton.Visible = false;
            content.Controls.Add(secondaryButton);

            ContextMenuStrip trayMenu = new ContextMenuStrip();
            trayMenu.Items.Add("Open Bangla OCR", null, delegate { OpenApplication(); });
            trayMenu.Items.Add("Show launcher", null, delegate { ShowLauncher(); });
            trayMenu.Items.Add(new ToolStripSeparator());
            trayMenu.Items.Add("Exit", null, delegate { ExitApplication(); });

            trayIcon = new NotifyIcon();
            trayIcon.Icon = Icon ?? SystemIcons.Application;
            trayIcon.Text = "Bangla OCR";
            trayIcon.ContextMenuStrip = trayMenu;
            trayIcon.DoubleClick += delegate { ShowLauncher(); };

            FormClosing += OnFormClosing;
            Shown += delegate { BeginStartup(); };
        }

        private Label CreateLabel(string value, float size, FontStyle style, Color color)
        {
            Label label = new Label();
            label.Text = value;
            label.Font = new Font("Segoe UI", size, style, GraphicsUnit.Point);
            label.ForeColor = color;
            label.BackColor = surface;
            return label;
        }

        private Button CreateButton(string value, bool primary)
        {
            Button button = new Button();
            button.Text = value;
            button.Font = new Font("Segoe UI", 9.5F, primary ? FontStyle.Bold : FontStyle.Regular);
            button.Size = primary ? new Size(154, 40) : new Size(132, 40);
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderSize = primary ? 0 : 1;
            button.FlatAppearance.BorderColor = Color.FromArgb(217, 221, 227);
            button.BackColor = primary ? accent : surface;
            button.ForeColor = primary ? Color.White : text;
            button.Cursor = Cursors.Hand;
            return button;
        }

        private void BeginStartup()
        {
            if (starting)
            {
                return;
            }
            starting = true;
            ready = false;
            ShowButtons(false, false);
            UpdateStatus("Checking the local service", "Looking for an existing Bangla OCR session.", 18, false, false);
            Thread worker = new Thread(StartBackendWorker);
            worker.IsBackground = true;
            worker.Start();
        }

        private void StartBackendWorker()
        {
            HealthResult probe = ProbeHealth(900);
            if (probe.State == "ready")
            {
                SetReady("The existing local session is available at " + Address);
                return;
            }
            if (probe.State == "occupied")
            {
                SetFailure("The local address is already in use", probe.Detail);
                return;
            }

            UpdateStatus("Starting the local service", "This usually takes a few seconds.", 45, true, false);
            try
            {
                StartBackendProcess();
            }
            catch (Exception exception)
            {
                WriteLog(exception.ToString());
                SetFailure("Bangla OCR could not start", exception.Message);
                return;
            }

            DateTime deadline = DateTime.UtcNow.AddSeconds(60);
            while (!exiting && DateTime.UtcNow < deadline)
            {
                if (backend == null || backend.HasExited)
                {
                    SetFailure("The local service stopped during startup", "Open the application log for technical details.");
                    return;
                }
                probe = ProbeHealth(900);
                if (probe.State == "ready")
                {
                    SetReady("Open the local workspace at " + Address);
                    return;
                }
                if (probe.State == "occupied")
                {
                    StopOwnedBackend();
                    SetFailure("The local address became unavailable", probe.Detail);
                    return;
                }
                Thread.Sleep(300);
            }

            if (!exiting)
            {
                StopOwnedBackend();
                SetFailure("Bangla OCR took too long to start", "The service was stopped safely. Check the log, then retry.");
            }
        }

        private void StartBackendProcess()
        {
            string[] candidates =
            {
                Path.Combine(applicationRoot, "runtime", "python", "python.exe"),
                Path.Combine(applicationRoot, ".venv", "Scripts", "python.exe")
            };
            string python = null;
            foreach (string candidate in candidates)
            {
                if (File.Exists(candidate))
                {
                    python = candidate;
                    break;
                }
            }
            if (python == null)
            {
                throw new FileNotFoundException("The private Python runtime is missing. Repair the Bangla OCR installation.");
            }

            RotateLog();
            Directory.CreateDirectory(Path.GetDirectoryName(logPath));
            logWriter = new StreamWriter(new FileStream(logPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), new UTF8Encoding(false));
            logWriter.AutoFlush = true;

            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = python;
            start.Arguments = "-m bangla_ocr app --host " + Host + " --port " + Port + " --no-browser";
            start.WorkingDirectory = applicationRoot;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            start.EnvironmentVariables["BANGLA_OCR_HOME"] = applicationRoot;
            start.EnvironmentVariables["PYTHONUTF8"] = "1";

            backend = new Process();
            backend.StartInfo = start;
            backend.EnableRaisingEvents = true;
            backend.OutputDataReceived += delegate(object sender, DataReceivedEventArgs args) { if (args.Data != null) WriteLog(args.Data); };
            backend.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args) { if (args.Data != null) WriteLog(args.Data); };
            backend.Exited += delegate
            {
                if (!exiting && ready)
                {
                    SetFailure("The local service stopped", "Open the application log, then restart Bangla OCR.");
                }
            };
            if (!backend.Start())
            {
                throw new InvalidOperationException("Windows did not start the local service.");
            }
            backend.BeginOutputReadLine();
            backend.BeginErrorReadLine();
        }

        private HealthResult ProbeHealth(int timeoutMilliseconds)
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(Address + "/api/health");
                request.Timeout = timeoutMilliseconds;
                request.ReadWriteTimeout = timeoutMilliseconds;
                request.Proxy = null;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                {
                    string body = reader.ReadToEnd();
                    JavaScriptSerializer serializer = new JavaScriptSerializer();
                    Dictionary<string, object> value = serializer.Deserialize<Dictionary<string, object>>(body);
                    object application;
                    object status;
                    if (value.TryGetValue("application", out application) && Convert.ToString(application) == "bangla-ocr" &&
                        value.TryGetValue("status", out status) && Convert.ToString(status) == "ready")
                    {
                        object reportedRoot;
                        if (!value.TryGetValue("application_root", out reportedRoot) ||
                            !SameDirectory(Convert.ToString(reportedRoot), applicationRoot))
                        {
                            return new HealthResult
                            {
                                State = "occupied",
                                Detail = "A different Bangla OCR installation is using the local address."
                            };
                        }
                        object activeJob;
                        bool active = value.TryGetValue("active_job", out activeJob) && Convert.ToBoolean(activeJob);
                        return new HealthResult { State = "ready", ActiveJob = active };
                    }
                    return new HealthResult { State = "occupied", Detail = "Unexpected health response." };
                }
            }
            catch (WebException exception)
            {
                if (exception.Response != null)
                {
                    return new HealthResult { State = "occupied", Detail = exception.Message };
                }
                return new HealthResult { State = "offline", Detail = exception.Message };
            }
            catch (Exception exception)
            {
                return new HealthResult { State = "occupied", Detail = exception.Message };
            }
        }

        private static bool SameDirectory(string first, string second)
        {
            try
            {
                string left = Path.GetFullPath(first).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                string right = Path.GetFullPath(second).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                return string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception)
            {
                return false;
            }
        }

        private void SetReady(string detail)
        {
            ready = true;
            starting = false;
            UpdateStatus("Bangla OCR is ready", detail, 100, false, false);
            RunOnUi(delegate { ShowButtons(true, true); trayIcon.Visible = true; });
        }

        private void SetFailure(string title, string detail)
        {
            ready = false;
            starting = false;
            UpdateStatus(title, detail, 0, false, true);
            RunOnUi(delegate
            {
                primaryButton.Text = "Retry";
                primaryButton.Click -= OpenButtonClick;
                primaryButton.Click += RetryButtonClick;
                secondaryButton.Text = "Open log";
                secondaryButton.Click -= CopyButtonClick;
                secondaryButton.Click += LogButtonClick;
                ShowButtons(true, true);
            });
        }

        private void OpenButtonClick(object sender, EventArgs args) { OpenApplication(); }
        private void CopyButtonClick(object sender, EventArgs args) { CopyAddress(); }
        private void RetryButtonClick(object sender, EventArgs args)
        {
            primaryButton.Click -= RetryButtonClick;
            secondaryButton.Click -= LogButtonClick;
            primaryButton.Text = "Open Bangla OCR";
            secondaryButton.Text = "Copy address";
            primaryButton.Click += OpenButtonClick;
            secondaryButton.Click += CopyButtonClick;
            BeginStartup();
        }
        private void LogButtonClick(object sender, EventArgs args) { OpenLog(); }

        private void UpdateStatus(string title, string detail, int value, bool marquee, bool isError)
        {
            RunOnUi(delegate
            {
                statusTitle.Text = title;
                statusDetail.Text = detail;
                statusDetail.ForeColor = isError ? danger : muted;
                progress.Style = marquee ? ProgressBarStyle.Marquee : ProgressBarStyle.Continuous;
                if (!marquee)
                {
                    progress.Value = Math.Max(0, Math.Min(100, value));
                }
            });
        }

        private void ShowButtons(bool primary, bool secondary)
        {
            primaryButton.Visible = primary;
            secondaryButton.Visible = secondary;
        }

        private void OpenApplication()
        {
            try
            {
                Process.Start(new ProcessStartInfo(Address) { UseShellExecute = true });
            }
            catch (Exception exception)
            {
                MessageBox.Show(this, exception.Message, "Could not open the browser", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void CopyAddress()
        {
            Clipboard.SetText(Address);
            statusDetail.Text = "Copied " + Address;
        }

        private void OpenLog()
        {
            if (!File.Exists(logPath))
            {
                MessageBox.Show(this, "No application log has been created yet.", "Bangla OCR", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            Process.Start(new ProcessStartInfo(logPath) { UseShellExecute = true });
        }

        private void ShowLauncher()
        {
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
        }

        private void OnFormClosing(object sender, FormClosingEventArgs args)
        {
            if (exiting || args.CloseReason == CloseReason.WindowsShutDown)
            {
                exiting = true;
                StopOwnedBackend();
                trayIcon.Visible = false;
                return;
            }
            args.Cancel = true;
            Hide();
            trayIcon.Visible = true;
            if (!trayMessageShown)
            {
                trayIcon.ShowBalloonTip(2500, "Bangla OCR is still running", "Use the tray icon to open or exit the local application.", ToolTipIcon.Info);
                trayMessageShown = true;
            }
        }

        private void ExitApplication()
        {
            HealthResult health = ProbeHealth(1200);
            if (health.State == "ready" && health.ActiveJob)
            {
                DialogResult choice = MessageBox.Show(
                    this,
                    "OCR processing is still running. Stop now and resume the job later?",
                    "Stop Bangla OCR?",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning
                );
                if (choice != DialogResult.Yes)
                {
                    return;
                }
            }
            exiting = true;
            StopOwnedBackend();
            trayIcon.Visible = false;
            Close();
        }

        private void StopOwnedBackend()
        {
            Process owned = backend;
            if (owned == null)
            {
                return;
            }
            try
            {
                if (!owned.HasExited)
                {
                    Process killer = Process.Start(new ProcessStartInfo
                    {
                        FileName = "taskkill.exe",
                        Arguments = "/PID " + owned.Id + " /T /F",
                        UseShellExecute = false,
                        CreateNoWindow = true
                    });
                    if (killer != null)
                    {
                        killer.WaitForExit(5000);
                    }
                }
            }
            catch (Exception exception)
            {
                WriteLog("Shutdown error: " + exception);
            }
            finally
            {
                backend = null;
                lock (this)
                {
                    if (logWriter != null)
                    {
                        logWriter.Dispose();
                        logWriter = null;
                    }
                }
            }
        }

        private void RotateLog()
        {
            if (!File.Exists(logPath) || new FileInfo(logPath).Length < 8L * 1024L * 1024L)
            {
                return;
            }
            for (int index = 3; index >= 1; index--)
            {
                string source = index == 1 ? logPath : logPath + "." + (index - 1);
                string destination = logPath + "." + index;
                if (!File.Exists(source))
                {
                    continue;
                }
                if (File.Exists(destination))
                {
                    File.Delete(destination);
                }
                File.Move(source, destination);
            }
        }

        private void WriteLog(string value)
        {
            lock (this)
            {
                if (logWriter != null)
                {
                    logWriter.WriteLine(DateTime.UtcNow.ToString("o") + " " + value);
                }
            }
        }

        private void RunOnUi(MethodInvoker action)
        {
            if (IsDisposed || exiting)
            {
                return;
            }
            if (InvokeRequired)
            {
                try { BeginInvoke(action); } catch (InvalidOperationException) { }
            }
            else
            {
                action();
            }
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new LauncherForm());
        }
    }
}
