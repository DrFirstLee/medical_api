


## 서버 ssh 접속을 위해서
# SSH 서버 설치 상태 확인 및 설치
https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse?tabs=gui&pivots=windows-11 
애서 ssh 설치하기

관리자권한으로 아래명령어 실해

Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH*'
PS C:\WINDOWS\system32> Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0


ssh 접속법
cloudflared access tcp --hostname warranties-fraction-stretch-caroline.trycloudflare.com --url localhost:2222