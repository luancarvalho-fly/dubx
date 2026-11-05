' Abre o DUBX como aplicativo, sem piscar terminal.
' 23/09/2026: caminho RELATIVO a este arquivo (antes era fixo no PC do Luan),
' pra funcionar em qualquer pasta / qualquer PC.
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
Set s = CreateObject("WScript.Shell")
s.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & base & "\abrir.ps1""", 0, False
