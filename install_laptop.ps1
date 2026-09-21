# Installs Python packages and puts a MultiTranslate shortcut on the Desktop and in the Start Menu.
# Run:  powershell -ExecutionPolicy Bypass -File install_laptop.ps1
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
python -m pip install -r "$root\requirements.txt" | Out-Null

# WScript.Shell cannot save into folders with non-ASCII names (e.g. OneDrive "ドキュメント"),
# so the shortcut is built in an ASCII temp path and then copied.
$tmp = Join-Path $env:TEMP "MultiTranslate.lnk"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($tmp)
$lnk.TargetPath = "$root\MultiTranslate.bat"
$lnk.WorkingDirectory = $root
$lnk.IconLocation = "$root\static\icon.ico"
$lnk.Description = "Translate files, folders and sheets into many languages"
$lnk.Save()

$targets = @([Environment]::GetFolderPath("Desktop"), "$env:USERPROFILE\Desktop",
             "$env:APPDATA\Microsoft\Windows\Start Menu\Programs") | Select-Object -Unique
foreach ($dir in $targets) {
    if (Test-Path $dir) {
        Copy-Item $tmp (Join-Path $dir "MultiTranslate.lnk") -Force
        Write-Host "Shortcut: $dir\MultiTranslate.lnk"
    }
}
Remove-Item $tmp -Force
Write-Host "Done. Double-click 'MultiTranslate' on the Desktop to start."
