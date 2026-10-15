' JARVIS Mark VI - Completely Silent Background Launcher
' Runs JARVIS Server + Background Wake-Word & Hotkey Daemon with zero console windows.
Set WshShell = CreateObject("WScript.Shell")
strPath = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
WshShell.CurrentDirectory = strPath

If CreateObject("Scripting.FileSystemObject").FileExists(strPath & "\.venv\Scripts\pythonw.exe") Then
    WshShell.Run Chr(34) & strPath & "\.venv\Scripts\pythonw.exe" & Chr(34) & " -m uvicorn backend.main:app --host 0.0.0.0 --port 8000", 0, False
Else
    WshShell.Run "pythonw.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000", 0, False
End If
