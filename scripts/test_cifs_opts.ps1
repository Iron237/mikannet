# 逐组测试 CIFS 挂载参数,找到目录枚举正常的组合
param(
    [string]$Device = $env:CIFS_TEST_DEVICE,
    [string]$User = $env:CIFS_TEST_USER,
    [string]$Password = $env:CIFS_TEST_PASSWORD
)
if (-not $Device -or -not $User -or -not $Password) {
    throw "请设置 CIFS_TEST_DEVICE / CIFS_TEST_USER / CIFS_TEST_PASSWORD"
}
$base = "username=$User,password=$Password,uid=1000,gid=1000,iocharset=utf8"
$variants = @(
    @{name="noserverino";      o="$base,vers=3.0,noserverino"},
    @{name="v311-noserverino"; o="$base,vers=3.1.1,noserverino"},
    @{name="v21";              o="$base,vers=2.1"},
    @{name="nohandlecache";    o="$base,vers=3.0,nohandlecache"}
)
foreach ($v in $variants) {
    $vol = "cifs-test-" + $v.name
    docker volume rm $vol 2>$null | Out-Null
    docker volume create --driver local --opt type=cifs --opt "o=$($v.o)" --opt "device=$Device" $vol | Out-Null
    $r = docker run --rm -v "${vol}:/mnt" busybox sh -c "echo x > /mnt/.t && ls /mnt >/dev/null 2>&1 && echo LS_OK || echo LS_FAIL; rm -f /mnt/.t" 2>&1
    Write-Output "$($v.name): $r"
    docker volume rm $vol 2>$null | Out-Null
}
