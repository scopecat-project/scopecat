"""PowerShell bridge to the Unicode Shell Link API (no WSH ANSI paths)."""

# IShellLinkW vtable order follows shobjidl_core.h. IPersistFile is supplied by
# .NET; all paths are read as JSON data, never inserted into this program.
CREATE_SHORTCUT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class ShellLink { }

[ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                 int count, IntPtr findData, uint flags);
    void GetIDList(out IntPtr pidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text,
                        int count);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string text);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                             int count);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string path);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text,
                      int count);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string text);
    void GetHotkey(out short key);
    void SetHotkey(short key);
    void GetShowCmd(out int command);
    void SetShowCmd(int command);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                         int count, out int index);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
    void Resolve(IntPtr window, uint flags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string path);
}

public static class ScopecatShortcut {
    public static void Save(string filename, string target,
                            string arguments, string home) {
        var link = (IShellLinkW)new ShellLink();
        try {
            link.SetPath(target);
            link.SetArguments(arguments);
            link.SetWorkingDirectory(home);
            ((IPersistFile)link).Save(filename, true);
        } finally {
            Marshal.ReleaseComObject(link);
        }
    }
}
'@
$c = Get-Content -LiteralPath .desktop-entry.json -Raw -Encoding UTF8 | ConvertFrom-Json
[ScopecatShortcut]::Save($c.link, $c.python, $c.arguments, $c.home)
"""
