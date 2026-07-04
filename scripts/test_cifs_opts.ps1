# 逐组测试 CIFS 挂载参数,找到目录枚举正常的组合
$device = "//192.168.2.4/Minecraft 独行侠/番剧/mikannet"
$variants = @(
    @{name="noserverino";      o="username=IG,password=IGhwcc237,uid=1000,gid=1000,iocharset=utf8,vers=3.0,noserverino"},
    @{name="v311-noserverino"; o="username=IG,password=IGhwcc237,uid=1000,gid=1000,iocharset=utf8,vers=3.1.1,noserverino"},
    @{name="v21";              o="username=IG,password=IGhwcc237,uid=1000,gid=1000,iocharset=utf8,vers=2.1"},
    @{name="nohandlecache";    o="username=IG,password=IGhwcc237,uid=1000,gid=1000,iocharset=utf8,vers=3.0,nohandlecache"}
)
foreach ($v in $variants) {
    $vol = "cifs-test-" + $v.name
    docker volume rm $vol 2>$null | Out-Null
    docker volume create --driver local --opt type=cifs --opt "o=$($v.o)" --opt "device=$device" $vol | Out-Null
    $r = docker run --rm -v "${vol}:/mnt" busybox sh -c "echo x > /mnt/.t && ls /mnt >/dev/null 2>&1 && echo LS_OK || echo LS_FAIL; rm -f /mnt/.t" 2>&1
    Write-Output "$($v.name): $r"
    docker volume rm $vol 2>$null | Out-Null
}
