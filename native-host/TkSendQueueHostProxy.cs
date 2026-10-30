using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;

namespace TkSend {
  // No-console Native Messaging proxy: Chrome <-> this exe <-> pythonw host.py
  // Avoids the black cmd.exe window from tk_send_queue_host.bat.
  static class Program {
    static string Dir() {
      try { return Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location); }
      catch { return AppDomain.CurrentDomain.BaseDirectory; }
    }

    static string FindPythonw(string dir) {
      string[] candidates = new string[] {
        Path.Combine(Directory.GetParent(dir).FullName, "runtime", "pythonw.exe"),
        Path.Combine(dir, "pythonw.exe"),
        Environment.ExpandEnvironmentVariables("%LOCALAPPDATA%\\Microsoft\\WindowsApps\\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\\pythonw.exe"),
        Environment.ExpandEnvironmentVariables("%LOCALAPPDATA%\\Microsoft\\WindowsApps\\pythonw.exe"),
        "pythonw.exe",
        "python.exe"
      };
      foreach (string c in candidates) {
        try {
          if (c.IndexOf('\\') >= 0 || c.IndexOf('/') >= 0) {
            if (File.Exists(c)) return c;
          } else {
            return c; // let PATH resolve
          }
        } catch {}
      }
      return "pythonw.exe";
    }

    static int Main(string[] args) {
      string dir = Dir();
      string script = Path.Combine(dir, "tk_send_queue_host.py");
      if (!File.Exists(script)) return 2;
      string py = FindPythonw(dir);
      ProcessStartInfo psi = new ProcessStartInfo();
      psi.FileName = py;
      psi.Arguments = "\"" + script + "\"";
      psi.WorkingDirectory = Directory.GetParent(dir).FullName; // send-plugin root
      psi.UseShellExecute = false;
      psi.CreateNoWindow = true;
      psi.RedirectStandardInput = true;
      psi.RedirectStandardOutput = true;
      psi.RedirectStandardError = true;
      Process p = null;
      try {
        p = Process.Start(psi);
      } catch (Exception ex) {
        try {
          string log = Path.Combine(Directory.GetParent(dir).FullName, "data", "logs", "native-host.log");
          Directory.CreateDirectory(Path.GetDirectoryName(log));
          File.AppendAllText(log, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " proxy start fail: " + ex.Message + "\n");
        } catch {}
        return 3;
      }
      // Pump Chrome stdin -> child stdin, child stdout -> Chrome stdout, child stderr -> log
      Thread tIn = new Thread(() => CopyStream(Console.OpenStandardInput(), p.StandardInput.BaseStream));
      Thread tOut = new Thread(() => CopyStream(p.StandardOutput.BaseStream, Console.OpenStandardOutput()));
      Thread tErr = new Thread(() => {
        try {
          string log = Path.Combine(Directory.GetParent(dir).FullName, "data", "logs", "native-host-stderr.log");
          Directory.CreateDirectory(Path.GetDirectoryName(log));
          using (FileStream fs = new FileStream(log, FileMode.Append, FileAccess.Write, FileShare.ReadWrite)) {
            CopyStream(p.StandardError.BaseStream, fs);
          }
        } catch {
          try { CopyStream(p.StandardError.BaseStream, Stream.Null); } catch {}
        }
      });
      tIn.IsBackground = true; tOut.IsBackground = true; tErr.IsBackground = true;
      tIn.Start(); tOut.Start(); tErr.Start();
      p.WaitForExit();
      return p.ExitCode;
    }

    static void CopyStream(Stream input, Stream output) {
      byte[] buf = new byte[4096];
      try {
        int n;
        while ((n = input.Read(buf, 0, buf.Length)) > 0) {
          output.Write(buf, 0, n);
          output.Flush();
        }
      } catch {}
      try { output.Flush(); } catch {}
      try { if (output != Console.OpenStandardOutput()) output.Close(); } catch {}
    }
  }
}
