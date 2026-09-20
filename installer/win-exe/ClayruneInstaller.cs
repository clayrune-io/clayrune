// Clayrune Installer — thin Windows .exe bootstrap.
//
// This is a faithful port of installer/Clayrune-Setup.bat to a native .exe so
// users get a double-clickable installer instead of a .bat (which downloads
// with a scarier SmartScreen warning and reads as untrustworthy).
//
// It is INTENTIONALLY thin: it does no install work itself. It discloses what
// will happen, downloads the canonical PowerShell bootstrap from the exact
// Git commit used to build this EXE, verifies its SHA-256, and runs that local
// file. Downloaded text is never piped directly into a shell.
//
// Built with the .NET Framework csc.exe that ships on every Windows 10/11 box
// (see build.ps1). Release builds are signed separately through Microsoft
// Artifact Signing; local builds remain unsigned and are for development only.
//
// Override the bootstrap URL and hash for testing with CLAYRUNE_PS1_URL and
// CLAYRUNE_PS1_SHA256.

using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

internal static class ClayruneInstaller
{
    // Exit codes returned by installer/install.ps1. This is a CONTRACT — see
    // the "EXIT CODES" block at the top of that file. Keep them in sync.
    private const int RcOk           = 0;
    private const int RcPrereq       = 1;  // explicit provider prerequisite
    private const int RcInstallStep  = 2;  // a [STEP n/5] failed
    private const int RcNotLoggedIn  = 3;  // explicit Claude choice not authed

    // Held for the process lifetime — see AcquireSingleInstance().
    private static Mutex _instanceLock;

    private static string Ps1Url()
    {
        var o = Environment.GetEnvironmentVariable("CLAYRUNE_PS1_URL");
        return string.IsNullOrWhiteSpace(o) ? BootstrapConfig.Url : o;
    }

    private static string Ps1Sha256()
    {
        var customUrl = Environment.GetEnvironmentVariable("CLAYRUNE_PS1_URL");
        if (string.IsNullOrWhiteSpace(customUrl)) return BootstrapConfig.Sha256;

        var customHash = Environment.GetEnvironmentVariable("CLAYRUNE_PS1_SHA256");
        if (string.IsNullOrWhiteSpace(customHash))
            throw new InvalidOperationException(
                "CLAYRUNE_PS1_SHA256 is required when CLAYRUNE_PS1_URL is overridden.");
        return customHash;
    }

    // One launch = one installer. A clean-VM smoke test (2026-07-23) saw a
    // single double-click produce TWO "Clayrune Installer" windows; the stray
    // second run raced the first one's `git clone`/`git pull` and left the
    // checkout diverged from origin, which then broke every later update.
    //
    // Note: there is no self-relaunch in this file, so the duplicate came from
    // outside it (Explorer double-activation / SmartScreen re-launch / a second
    // human double-click). A named mutex makes the symptom impossible whatever
    // the cause, and — more importantly — makes two concurrent installs writing
    // the same directory impossible.
    //
    // The loser exits IMMEDIATELY with no pause, so a spurious second window
    // closes on its own and the user is left looking at exactly one. The line
    // it prints is still visible when the exe is run from an existing console.
    // Never let a mutex problem block a real install: on any failure, proceed.
    private static bool AcquireSingleInstance()
    {
        try
        {
            bool createdNew;
            _instanceLock = new Mutex(true, @"Local\ClayruneInstaller.SingleInstance", out createdNew);
            return createdNew;
        }
        catch
        {
            return true;
        }
    }

    private static int Main()
    {
        try { Console.OutputEncoding = Encoding.UTF8; } catch { /* legacy console */ }
        Console.Title = "Clayrune Installer";

        if (!AcquireSingleInstance())
        {
            Console.WriteLine("Clayrune Installer is already running in another window.");
            return RcOk;
        }

        Console.WriteLine();
        Console.WriteLine("============================================================");
        Console.WriteLine("  Clayrune Installer");
        Console.WriteLine("============================================================");
        Console.WriteLine();
        Console.WriteLine("This will install Clayrune on this computer.");
        Console.WriteLine();
        Console.WriteLine("It will:");
        Console.WriteLine("  1. Ask which AI you work with (Claude Code / OpenAI Codex / Gemini)");
        Console.WriteLine("  2. Install Node.js LTS and Git for Windows (if missing)");
        Console.WriteLine("  3. Install that CLI");
        Console.WriteLine("  4. Ask you to sign in once, if the chosen CLI needs it");
        Console.WriteLine("  5. Clone Clayrune to %USERPROFILE%\\Clayrune");
        Console.WriteLine("  6. Set up Python dependencies + a Desktop shortcut");
        Console.WriteLine("  7. Open the dashboard in your browser");
        Console.WriteLine();
        Console.WriteLine("Estimated time: 5-10 minutes.");
        Console.WriteLine("Disk space: about 500 MB.");
        Console.WriteLine();
        Console.WriteLine("You can audit what runs by reading:");
        Console.WriteLine("  https://raw.githubusercontent.com/clayrune-io/clayrune/master/installer/install-prompt.md");
        Console.WriteLine();
        Pause("Press Enter to begin (or close this window to cancel) . . .");

        while (true)
        {
            Console.WriteLine();
            Console.WriteLine("Starting installer...");
            Console.WriteLine();

            int rc = RunBootstrap();

            Console.WriteLine();
            Console.WriteLine("============================================================");
            if (rc == 0)
            {
                Console.WriteLine("  Done.");
                Console.WriteLine();
                Console.WriteLine("  You'll find a \"Clayrune\" shortcut on your Desktop and in");
                Console.WriteLine("  your Start Menu. Double-click it any time to launch.");
                Console.WriteLine("============================================================");
                Console.WriteLine();
                Pause("Press Enter to close this window . . .");
                return 0;
            }

            Console.WriteLine("  Installer paused.");
            Console.WriteLine();

            // Diagnose the ACTUAL failure. Previously every non-zero exit was
            // reported as "you probably aren't logged in", which sent a user
            // through a pointless OAuth login while the real failure was git
            // (clean-VM smoke test, 2026-07-23) — they logged in and hit the
            // identical error. Offer [L] only when login is plausibly the fix.
            // Only install.ps1's explicit Claude auth branch returns 3. A
            // generic prerequisite failure must never steer a Codex/Qwen/
            // Gemini user into a Claude login window.
            bool offerLogin = (rc == RcNotLoggedIn);
            switch (rc)
            {
                case RcNotLoggedIn:
                    Console.WriteLine("  Claude CLI is installed but not logged in yet.");
                    Console.WriteLine("  We can handle the login for you - pick L below.");
                    break;
                case RcInstallStep:
                    Console.WriteLine("  An install step failed. Scroll up to the red line that");
                    Console.WriteLine("  starts with [STEP n/5] FAIL - it names the exact step and");
                    Console.WriteLine("  what to do about it.");
                    Console.WriteLine();
                    Console.WriteLine("  This is NOT a login problem, so logging in will not help.");
                    Console.WriteLine("  Common causes: no internet, git/Python missing or blocked");
                    Console.WriteLine("  by antivirus, or a damaged checkout in %USERPROFILE%\\Clayrune.");
                    break;
                case RcPrereq:
                    Console.WriteLine("  A prerequisite could not be installed (Node.js, Git, or the");
                    Console.WriteLine("  selected provider CLI). The output above says which one and how to");
                    Console.WriteLine("  install it by hand.");
                    Console.WriteLine();
                    Console.WriteLine("  If the output above says \"not authenticated\", pick L.");
                    Console.WriteLine("  Otherwise install the missing piece first, then pick R.");
                    break;
                default:
                    Console.WriteLine("  The installer stopped with an unexpected error (exit code "
                                      + rc + ").");
                    Console.WriteLine("  The full output above shows what happened.");
                    offerLogin = false;
                    break;
            }
            Console.WriteLine("============================================================");
            Console.WriteLine();
            Console.WriteLine("  What now?");
            if (offerLogin)
                Console.WriteLine("    [L] Log me in to Claude now (opens browser, then re-runs installer)");
            Console.WriteLine("    [R] Retry the installer (if you've already fixed the issue)");
            Console.WriteLine("    [Q] Quit and close this window");
            Console.WriteLine();

            string allowed = offerLogin ? "LRQ" : "RQ";
            string prompt = offerLogin
                ? "Press L, R, or Q then Enter: "
                : "Press R or Q then Enter: ";
            char choice = ReadChoice(prompt, allowed);
            if (choice == 'Q') return rc;
            if (choice == 'L') DoLogin();
            // L and R both fall through to the top of the loop (re-run).
        }
    }

    // Download the canonical bootstrap as data, verify the build-pinned hash,
    // then execute the verified local file. Never pipe downloaded text into a
    // shell: that removes the integrity boundary and resembles malware.
    private static int RunBootstrap()
    {
        string scriptPath = Path.Combine(
            Path.GetTempPath(), "Clayrune-install-" + Guid.NewGuid().ToString("N") + ".ps1");
        try
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            using (var client = new WebClient())
            {
                client.Headers[HttpRequestHeader.UserAgent] = "Clayrune-Installer";
                client.DownloadFile(Ps1Url(), scriptPath);
            }

            string actualHash;
            using (var stream = File.OpenRead(scriptPath))
            using (var sha = SHA256.Create())
                actualHash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "");

            string expectedHash = Ps1Sha256().Replace("-", "").Trim();
            if (!actualHash.Equals(expectedHash, StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine();
                Console.WriteLine("SECURITY ERROR: installer bootstrap hash mismatch.");
                Console.WriteLine("The downloaded file was not executed.");
                return RcInstallStep;
            }

            var psi = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy RemoteSigned -File \"" + scriptPath + "\"",
                UseShellExecute = false,
                WorkingDirectory = Path.GetTempPath(),
            };
            using (var p = Process.Start(psi))
            {
                p.WaitForExit();
                return p.ExitCode;
            }
        }
        catch (Exception e)
        {
            Console.WriteLine();
            Console.WriteLine("Could not download or launch the verified installer: " + e.Message);
            return 1;
        }
        finally
        {
            try { if (File.Exists(scriptPath)) File.Delete(scriptPath); }
            catch { /* best-effort cleanup of a verified temporary file */ }
        }
    }

    // Spawn `claude /login` in a SEPARATE window and block until it closes.
    // PATH is rebuilt from the registry because install.ps1 just added
    // %APPDATA%\npm (where Claude CLI lives) to the USER PATH, but THIS
    // process inherited its PATH at launch — before that change — so a child
    // would not find `claude`. PowerShell can rebuild $env:Path per call.
    private static void DoLogin()
    {
        Console.WriteLine();
        Console.WriteLine("============================================================");
        Console.WriteLine("  Launching Claude login in a new window");
        Console.WriteLine("============================================================");
        Console.WriteLine();
        Console.WriteLine("A second window will open running `claude /login`.");
        Console.WriteLine("  1. A browser opens. Sign in with your Anthropic account");
        Console.WriteLine("     (Claude Pro/Max OAuth), or paste an API key when prompted.");
        Console.WriteLine("  2. When you see \"Logged in successfully\", type:  exit");
        Console.WriteLine("  3. The login window closes on its own.");
        Console.WriteLine();
        Console.WriteLine("This window keeps running and picks up where you left off.");
        Console.WriteLine();
        Pause("Press Enter to open the login window . . .");

        string inner =
            "$env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + " +
            "[System.Environment]::GetEnvironmentVariable('Path','User'); " +
            "$cl = Get-Command claude -ErrorAction SilentlyContinue; " +
            "if (-not $cl) { Write-Host ''; Write-Host 'ERROR: claude command not found.' " +
            "-ForegroundColor Red; Write-Host 'The installer should have installed it. " +
            "Close this window and pick [R] in the main window to retry.' } " +
            "else { Write-Host \"\"\"Found claude at: $($cl.Path)\"\"\" -ForegroundColor DarkGray; " +
            "Write-Host ''; & claude /login }; " +
            "Write-Host ''; Read-Host 'Press Enter to close this window'";

        // `cmd /c start "title" /WAIT powershell ...` gives the login its own
        // console window and blocks us until it closes.
        var psi = new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = "/c start \"Clayrune - Claude Login\" /WAIT powershell.exe " +
                        "-NoProfile -Command \"" +
                        inner.Replace("\"", "\\\"") + "\"",
            UseShellExecute = false,
        };
        try
        {
            using (var p = Process.Start(psi)) { p.WaitForExit(); }
        }
        catch (Exception e)
        {
            Console.WriteLine("Could not launch the login window: " + e.Message);
        }

        Console.WriteLine();
        Console.WriteLine("============================================================");
        Console.WriteLine("Login window closed. Retrying the installer...");
        Console.WriteLine("============================================================");
    }

    private static void Pause(string prompt)
    {
        Console.Write(prompt);
        try { Console.ReadLine(); } catch { /* no stdin (rare) */ }
    }

    private static char ReadChoice(string prompt, string allowed)
    {
        while (true)
        {
            Console.Write(prompt);
            string line = Console.ReadLine();
            // null == stdin at EOF (closed/redirected). Don't spin forever:
            // the safe interpretation of "no input" here is quit.
            if (line == null) return 'Q';
            if (line.Trim().Length > 0)
            {
                char c = char.ToUpperInvariant(line.Trim()[0]);
                if (allowed.IndexOf(c) >= 0) return c;
            }
            Console.WriteLine("Please enter one of: " + string.Join(", ", allowed.ToCharArray()));
        }
    }
}
