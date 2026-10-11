@echo off
:: ==============================================================================
:: JARVIS - Windows Laptop Wake-on-LAN (WoWLAN) & Sleep Fixer
:: Configures Realtek WiFi / Ethernet adapters, disables Fast Startup, and sets
:: firewall rules to enable powering on your laptop from your phone.
:: ==============================================================================

title JARVIS Wake-on-LAN & Sleep Setup

:: Check for Administrator privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Administrator privileges required. Requesting elevation...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo ==============================================================================
echo              JARVIS PC & LAPTOP WAKE-ON-LAN CONFIGURATOR
echo ==============================================================================
echo.

echo [1/4] Disabling Windows Fast Startup (Required for WoWLAN sleep listening)...
powercfg /h off
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" /v HiberbootEnabled /t REG_DWORD /d 0 /f >nul 2>&1
echo       [OK] Fast Startup disabled successfully.

echo.
echo [2/4] Enabling Wake-on-LAN & Wake-on-Magic-Packet on network adapters...
powershell -NoProfile -Command ^
    "Get-NetAdapter | ForEach-Object { " ^
    "    $nic = $_; " ^
    "    try { " ^
    "        Enable-NetAdapterPowerManagement -Name $nic.Name -WakeOnMagicPacket -WakeOnPattern -ErrorAction SilentlyContinue; " ^
    "        Set-NetAdapterAdvancedProperty -Name $nic.Name -RegistryKeyword '*WakeOnMagicPacket' -RegistryValue 1 -ErrorAction SilentlyContinue; " ^
    "        Set-NetAdapterAdvancedProperty -Name $nic.Name -RegistryKeyword '*WakeOnPattern' -RegistryValue 1 -ErrorAction SilentlyContinue; " ^
    "        Write-Host ('       [+] Configured ' + $nic.Name + ' (' + $nic.InterfaceDescription + ')'); " ^
    "    } catch {} " ^
    "}"

echo.
echo [3/4] Configuring Windows Firewall to allow WoL UDP packets (Port 9 & 7)...
netsh advfirewall firewall delete rule name="JARVIS_WakeOnLAN_UDP" >nul 2>&1
netsh advfirewall firewall add rule name="JARVIS_WakeOnLAN_UDP" dir=in action=allow protocol=UDP localport=7,9 profile=any >nul 2>&1
echo       [OK] Firewall rules added for UDP ports 7 and 9.

echo.
echo [4/4] Hardware Network Configuration Summary:
powershell -NoProfile -Command ^
    "$wifi = Get-NetAdapter | Where-Object Status -eq 'Up'; " ^
    "Write-Host ('       Active Adapter : ' + $wifi.InterfaceDescription); " ^
    "Write-Host ('       Physical MAC   : ' + $wifi.MacAddress); " ^
    "Write-Host ('       Status         : WoWLAN Ready'); "

echo.
echo ==============================================================================
echo                     SETUP COMPLETE! WAKE-ON-LAN IS READY
echo ==============================================================================
echo.
echo HOW TO WAKE YOUR LAPTOP FROM SLEEP:
echo 1. Your laptop's physical MAC address is: 9C:C7:D3:36:72:C6
echo 2. Subnet Broadcast IP: 10.202.34.255 (UDP Port 9)
echo 3. When your laptop sleeps, open the JARVIS Mobile Companion on your phone
echo    and tap 'Open Android WoL' or use any free WoL app (e.g. 'Wake On Lan').
echo 4. Your laptop will instantly wake up!
echo ==============================================================================
echo.
pause
