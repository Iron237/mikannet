#!/bin/bash
# 逐组测试 CIFS 挂载参数,找到目录枚举(ls)正常的组合
device='//192.168.2.4/Minecraft 独行侠/番剧/mikannet'
base='username=IG,password=IGhwcc237,uid=1000,gid=1000,iocharset=utf8'

declare -A variants=(
  [v30-noserverino]="$base,vers=3.0,noserverino"
  [v311-noserverino]="$base,vers=3.1.1,noserverino"
  [v21]="$base,vers=2.1"
  [v30-nohandlecache]="$base,vers=3.0,nohandlecache"
  [v30-cache-none]="$base,vers=3.0,cache=none,noserverino"
)

for name in "${!variants[@]}"; do
  vol="cifs-test-$name"
  docker volume rm "$vol" >/dev/null 2>&1
  docker volume create --driver local \
    --opt type=cifs \
    --opt "o=${variants[$name]}" \
    --opt "device=$device" "$vol" >/dev/null
  result=$(docker run --rm -v "$vol:/mnt" busybox sh -c \
    'echo x > /mnt/.t 2>/dev/null; ls /mnt >/dev/null 2>&1 && echo LS_OK || echo LS_FAIL; rm -f /mnt/.t' 2>&1 | tail -1)
  echo "$name: $result"
  docker volume rm "$vol" >/dev/null 2>&1
done