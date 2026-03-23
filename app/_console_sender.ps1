param(
    [string]$targetPid,
    [string]$b64Text,
    [string]$sendEnter
)
$inputBytes = [System.Convert]::FromBase64String($b64Text)
$inputText  = [System.Text.Encoding]::UTF8.GetString($inputBytes)

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[StructLayout(LayoutKind.Explicit)]
public struct KEY_EVENT_RECORD {
    [FieldOffset(0)] public bool bKeyDown;
    [FieldOffset(4)] public short wRepeatCount;
    [FieldOffset(6)] public short wVirtualKeyCode;
    [FieldOffset(8)] public short wVirtualScanCode;
    [FieldOffset(10)] public char UnicodeChar;
    [FieldOffset(12)] public int dwControlKeyState;
}

[StructLayout(LayoutKind.Explicit)]
public struct INPUT_RECORD {
    [FieldOffset(0)] public short EventType;
    [FieldOffset(4)] public KEY_EVENT_RECORD KeyEvent;
}

public class ConsoleIO {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool FreeConsole();

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AttachConsole(uint dwProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool WriteConsoleInput(
        IntPtr hConsoleInput,
        INPUT_RECORD[] lpBuffer,
        uint nLength,
        out uint lpNumberOfEventsWritten);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool FlushConsoleInputBuffer(IntPtr hConsoleInput);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AllocConsole();

    [DllImport("user32.dll")]
    public static extern short VkKeyScan(char ch);

    public static void SendString(uint pid, string text, bool sendEnter) {
        Exception lastEx = null;
        for (int attempt = 0; attempt < 3; attempt++) {
            if (attempt > 0) System.Threading.Thread.Sleep(150);
            try {
                FreeConsole();
                if (!AttachConsole(pid)) {
                    int err = Marshal.GetLastWin32Error();
                    AllocConsole();
                    if (attempt == 2) throw new Exception("AttachConsole failed for pid " + pid + ", error " + err);
                    lastEx = new Exception("AttachConsole failed, error " + err);
                    continue;
                }
                try {
                    System.Threading.Thread.Sleep(200);
                    IntPtr hInput = GetStdHandle(-10);
                    if (hInput == IntPtr.Zero || hInput == (IntPtr)(-1)) {
                        if (attempt == 2) throw new Exception("GetStdHandle returned invalid handle");
                        lastEx = new Exception("GetStdHandle returned invalid handle");
                        continue;
                    }

                    // Note: do NOT flush the input buffer — it discards any
                    // keystrokes the user may have typed in the terminal.

                    int totalEvents = text.Length * 2 + (sendEnter ? 2 : 0);
                    INPUT_RECORD[] allRecs = new INPUT_RECORD[totalEvents];
                    int idx = 0;

                    foreach (char ch in text) {
                        short vk;
                        if ((int)ch < 32) {
                            vk = (short)(int)ch;
                        } else {
                            short vkResult = VkKeyScan(ch);
                            vk = (vkResult == -1) ? (short)0 : (short)(vkResult & 0xFF);
                        }

                        allRecs[idx].EventType = 1;
                        allRecs[idx].KeyEvent.bKeyDown = true;
                        allRecs[idx].KeyEvent.wRepeatCount = 1;
                        allRecs[idx].KeyEvent.wVirtualKeyCode = vk;
                        allRecs[idx].KeyEvent.UnicodeChar = ch;
                        idx++;
                        allRecs[idx].EventType = 1;
                        allRecs[idx].KeyEvent.bKeyDown = false;
                        allRecs[idx].KeyEvent.wRepeatCount = 1;
                        allRecs[idx].KeyEvent.wVirtualKeyCode = vk;
                        allRecs[idx].KeyEvent.UnicodeChar = ch;
                        idx++;
                    }

                    if (sendEnter) {
                        allRecs[idx].EventType = 1;
                        allRecs[idx].KeyEvent.bKeyDown = true;
                        allRecs[idx].KeyEvent.wRepeatCount = 1;
                        allRecs[idx].KeyEvent.wVirtualKeyCode = 0x0D;
                        allRecs[idx].KeyEvent.UnicodeChar = (char)13;
                        idx++;
                        allRecs[idx].EventType = 1;
                        allRecs[idx].KeyEvent.bKeyDown = false;
                        allRecs[idx].KeyEvent.wRepeatCount = 1;
                        allRecs[idx].KeyEvent.wVirtualKeyCode = 0x0D;
                        allRecs[idx].KeyEvent.UnicodeChar = (char)13;
                        idx++;
                    }

                    uint totalToWrite = (uint)totalEvents;
                    uint totalWritten = 0;
                    int writeRetries = 0;
                    while (totalWritten < totalToWrite && writeRetries < 3) {
                        uint remaining = totalToWrite - totalWritten;
                        INPUT_RECORD[] slice;
                        if (totalWritten == 0) {
                            slice = allRecs;
                        } else {
                            slice = new INPUT_RECORD[remaining];
                            Array.Copy(allRecs, totalWritten, slice, 0, remaining);
                        }
                        uint written;
                        if (!WriteConsoleInput(hInput, slice, remaining, out written)) {
                            int err = Marshal.GetLastWin32Error();
                            throw new Exception("WriteConsoleInput failed, error " + err);
                        }
                        if (written == 0) {
                            System.Threading.Thread.Sleep(50);
                            writeRetries++;
                            continue;
                        }
                        totalWritten += written;
                        writeRetries = 0;
                    }
                    if (totalWritten < totalToWrite) {
                        throw new Exception("WriteConsoleInput partial: wrote " + totalWritten + " of " + totalToWrite);
                    }
                    return;
                } finally {
                    FreeConsole();
                    AllocConsole();
                }
            } catch (Exception ex) {
                lastEx = ex;
                if (attempt == 2) throw;
            }
        }
        if (lastEx != null) throw lastEx;
    }
}
'@

# Find the cmd.exe parent (the console host)
$cur = [int]$targetPid
$consolePid = $null
while ($cur -gt 4) {
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -EA Stop
        if ($proc.Name -eq 'cmd.exe') { $consolePid = $cur; break }
        $cur = [int]$proc.ParentProcessId
    } catch { break }
}
if (-not $consolePid) {
    throw "Session was not launched from a GUI terminal (no cmd.exe parent found). Cannot inject input directly."
}

[ConsoleIO]::SendString([uint32]$consolePid, $inputText, [bool][int]$sendEnter)
