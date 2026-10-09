param([ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')][string]$ContainerName = 'trade-graph-paper-recovered-dashboard-20261008')

$ErrorActionPreference = 'Stop'
$sessionReader = @'
import json, subprocess, sys
from pathlib import Path
container = json.loads(subprocess.check_output(['docker', 'inspect', sys.argv[1]]))[0]
state = next(m['Source'] for m in container['Mounts'] if m['Destination'] == '/var/lib/trade-graph')
document = json.loads((Path(state) / 'owner-session.json').read_text())
token = document['session_token']
if not isinstance(token, str) or not 1 <= len(token) <= 256:
    raise ValueError('Private owner session is unavailable')
print(token)
'@
$dashboardSession = & wsl.exe -u root -e python3 -c $sessionReader $ContainerName
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($dashboardSession)) {
    throw 'Unable to read the current private dashboard session.'
}
Set-Clipboard -Value $dashboardSession.Trim()
Remove-Variable dashboardSession
Write-Output 'Private dashboard session copied to the local clipboard. Paste it into the sign-in field.'
