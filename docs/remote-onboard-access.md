# 通过阿里云服务器远程访问本地 Onboard 页面

> 适用场景：你的 Mac/PC 部署了 OpenHarness 的 `onboard` 页面，希望在公司、咖啡店、手机网络等任意地方，通过访问阿里云服务器来访问这台电脑上的 onboard。

---

## 1. 你能得到什么

配置完成后，你在任何地方打开浏览器，输入：

```
http://你的阿里云IP:8090?token=你的Token
```

就能看到家里/公司里这台电脑上运行的 onboard 页面。

---

## 2. 前置条件

在动手前，请确认以下 4 件事已经具备：

| 条件 | 说明 | 如何确认 |
|------|------|---------|
| 本地已运行 onboard | 你的电脑上已经启动了 `onboard` 服务 | 浏览器访问 `http://localhost:8090` 能看到页面 |
| 可免密 SSH 登录阿里云 | 你的电脑能通过 SSH 密钥直接登录服务器，不需要输入密码 | 执行 `ssh aliyun` 能直接进去 |
| 知道阿里云的公网 IP | 例如 `106.15.189.47` | 在阿里云控制台查看 |
| 阿里云安全组可配置 | 你能在阿里云 ECS 控制台修改安全组规则 | 有阿里云账号即可 |

> **注意**：本文假设你的 SSH 配置里已经把阿里云服务器取名为 `aliyun`。如果你的 `~/.ssh/config` 里叫别的名字（比如 `ecs`），后文所有 `aliyun` 都替换成你的名字。

---

## 3. 整体原理（小白版）

### 3.1 问题：onboard 只跑在你的电脑上

onboard 默认监听 `0.0.0.0:8090`，意思是同一局域网内的设备可以访问它。但离开这个网络（比如手机用 4G/5G），就找不到你的电脑了。

### 3.2 解决思路：让阿里云服务器当你的"中转站"

你有一台有公网 IP 的阿里云服务器，互联网上的任何设备都能访问它。如果能让这台服务器把访问请求"转交"给你的电脑，问题就解决了。

### 3.3 关键技术：SSH 反向隧道

SSH 不仅能登录服务器，还能在服务器和你的电脑之间"挖一条隧道"。

想象你的电脑是 A，阿里云服务器是 B：

```
A（你的电脑，运行 onboard:8090）
   ↑
   │  SSH 反向隧道
   │  "服务器 B 的某个端口 ↔ 电脑 A 的 8090"
   ↓
B（阿里云服务器，有公网 IP）
   ↑
   │  互联网
   ↓
任何地方的浏览器
```

当你在浏览器访问 `B:8090` 时，请求会通过 SSH 隧道传到 A 的 8090，就像你坐在 A 旁边一样。

### 3.4 为什么需要多一个"Python 转发脚本"？

理想情况下，一条命令就够了：

```bash
ssh -R 0.0.0.0:8090:localhost:8090 aliyun
```

但这条命令要求服务器允许把反向隧道绑定到公网地址（`0.0.0.0`），这需要在 `/etc/ssh/sshd_config` 里开启 `GatewayPorts yes`。

**修改这个文件需要 root/sudo 权限**。如果你的阿里云账号没有 sudo，就只能让反向隧道绑定到服务器的 `127.0.0.1`（本机地址），外网仍然无法直接访问。

所以我们的折中方案是：

1. 先用 SSH 反向隧道把服务器的 `127.0.0.1:18090` 映射到电脑的 `localhost:8090`。
2. 再在服务器上运行一个 Python 小脚本，监听外网 `0.0.0.0:8090`，把请求转发到 `127.0.0.1:18090`。

完整链路如下：

```
浏览器
  → 106.15.189.47:8090（阿里云公网）
  → Python 转发脚本（0.0.0.0:8090 → 127.0.0.1:18090）
  → SSH 反向隧道（127.0.0.1:18090 → 你的电脑:8090）
  → onboard 页面
```

---

## 4. 详细操作步骤

### 步骤 1：确认本地 onboard 已在运行

在本地终端执行：

```bash
lsof -i :8090
```

如果看到类似下面的输出，说明 onboard 已经在监听：

```
COMMAND     PID  USER   FD   TYPE ...  TCP *:8090 (LISTEN)
```

如果没有，先启动 onboard（三选一）：

```bash
# 方式 1：通过 wolo
wolo onboard run

# 方式 2：通过 solo
solo onboard run

# 方式 3：直接运行 onboard 模块
uv run python -m onboard run
```

启动后，浏览器访问 `http://localhost:8090` 应该能看到 onboard 页面。

---

### 步骤 2：在阿里云服务器上创建转发脚本

**目的**：让服务器监听外网 `0.0.0.0:8090`，并把请求转给 SSH 反向隧道端口 `127.0.0.1:18090`。

SSH 登录到阿里云服务器：

```bash
ssh aliyun
```

在服务器上创建文件 `~/forward_onboard.py`，内容如下：

```python
#!/usr/bin/env python3
import asyncio

# 服务器对外监听的地址和端口
LOCAL_HOST = '0.0.0.0'
LOCAL_PORT = 8090

# 转发到 SSH 反向隧道在服务器本机开放的端口
REMOTE_HOST = '127.0.0.1'
REMOTE_PORT = 18090


async def forward(reader, writer):
    """把 reader 收到的数据原样写给 writer。"""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle(local_reader, local_writer):
    """每有一个外网连接进来，就建立到内部端口的连接，然后双向转发。"""
    peer = local_writer.get_extra_info('peername')
    print(f'Connection from {peer}', flush=True)
    try:
        remote_reader, remote_writer = await asyncio.open_connection(
            REMOTE_HOST, REMOTE_PORT
        )
        await asyncio.gather(
            forward(local_reader, remote_writer),
            forward(remote_reader, local_writer)
        )
    except Exception as e:
        print(f'Error handling {peer}: {e}', flush=True)
    finally:
        local_writer.close()


async def main():
    server = await asyncio.start_server(handle, LOCAL_HOST, LOCAL_PORT)
    addr = server.sockets[0].getsockname()
    print(
        f'Forwarding {LOCAL_HOST}:{LOCAL_PORT} -> {REMOTE_HOST}:{REMOTE_PORT}',
        flush=True
    )
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
```

保存后，给脚本执行权限：

```bash
chmod +x ~/forward_onboard.py
```

---

### 步骤 3：启动转发脚本

在服务器上执行：

```bash
nohup python3 ~/forward_onboard.py > /tmp/forward_onboard.log 2>&1 &
```

解释：

- `nohup`：让进程在你退出 SSH 后继续运行。
- `> /tmp/forward_onboard.log 2>&1`：把输出写入日志文件。
- `&`：放到后台运行。

验证是否启动成功：

```bash
ss -tlnp | grep 8090
```

应该看到类似：

```
LISTEN 0  100  0.0.0.0:8090  0.0.0.0:*  users:(("python3",pid=...,fd=6))
```

这表示服务器已经在公网监听 `8090` 端口。

查看日志确认：

```bash
cat /tmp/forward_onboard.log
```

应该输出：

```
Forwarding 0.0.0.0:8090 -> 127.0.0.1:18090
```

---

### 步骤 4：在本地建立 SSH 反向隧道

**目的**：让服务器(aliyun)本机的 `127.0.0.1:18090` 映射到你电脑(Mac)的 `localhost:8090`。

在你的 Mac/PC 本地终端执行：

```bash
ssh -f -N -R 127.0.0.1:18090:localhost:8090 aliyun
```

参数解释：

- `-f`：认证成功后放到后台运行。
- `-N`：不执行远程命令，只建立隧道。
- `-R 127.0.0.1:18090:localhost:8090`：反向隧道，意思是"在服务器上监听 `127.0.0.1:18090`，所有到这个端口的连接都转发到本地的 `localhost:8090`"。
- `aliyun`：你在 `~/.ssh/config` 里给阿里云服务器起的别名。

验证隧道是否建立：

```bash
ssh aliyun "ss -tlnp | grep 18090"
```

应该看到类似：

```
LISTEN 0  128  127.0.0.1:18090  0.0.0.0:*
```

同时在本地确认 ssh 进程存在：

```bash
ps aux | grep 'ssh -f -N -R' | grep -v grep
```

---

### 步骤 5：配置阿里云安全组放行 8090 端口

目前服务器已经在监听 `0.0.0.0:8090`，但阿里云默认的安全组规则会拦截外部访问。你需要手动放行。

#### 操作路径

1. 登录 [阿里云控制台](https://ecs.console.aliyun.com/)。
2. 进入 **云服务器 ECS** → **实例**。
3. 找到你的实例，点击 **管理**。
4. 在左侧菜单选择 **安全组**。
5. 点击对应安全组右侧的 **配置规则**。
6. 选择 **入方向** → **手动添加**（或 **快速添加**）。

#### 需要添加的规则

| 参数 | 值 |
|------|-----|
| 授权策略 | 允许 |
| 优先级 | 1（或默认） |
| 协议类型 | 自定义 TCP |
| 端口范围 | 8090/8090 |
| 授权对象 | 0.0.0.0/0 |
| 描述 | onboard remote access |

> **安全提示**：`0.0.0.0/0` 表示任何 IP 都能访问。如果你只想让特定网络访问，可以把授权对象改成你的公网 IP 段，例如 `123.45.67.89/32`。

保存后，等待几十秒生效。

---

### 步骤 6：获取 Token

onboard 有 Token Gate 认证，首次访问需要 token。

在本地终端执行以下任一命令查看 token：

```bash
# 方式 1：直接读取 onboard 的 secret 文件
cat ~/.onboard/secret

# 方式 2：通过 solo CLI
solo onboard token

# 方式 3：通过 wolo CLI
wolo onboard token
```

复制拿到的字符串，下一步会用到。

---

### 步骤 7：验证访问并正式使用

安全组生效后，先不要急着用浏览器，用 curl 在本地终端验证一下：

```bash
curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 \
  'http://106.15.189.47:8090/?token=你的Token'
```

把 `106.15.189.47` 换成你的阿里云 IP，把 `你的Token` 换成上一步拿到的字符串。

#### HTTP 状态码含义

| 返回码 | 含义 | 结论 |
|--------|------|------|
| `000` 或超时 | 请求根本没到达服务器 | 安全组没放行、端口没监听、或网络不通 |
| `401` | 到达了 onboard，但 token 不对或没传 | 检查 URL 里的 `?token=` 是否正确 |
| `302` | **认证通过，正在跳转到主页面** | ✅ 链路完全通了 |
| `200` | 页面内容已返回 | ✅ 通了（如果用 `-L` 跟随了重定向） |

#### 为什么 302 也是成功

onboard 的认证逻辑是：收到 `?token=xxx` 后，先给你设置 session cookie，然后发送一个 `302 Found` 重定向，把你带到没有 token 参数的首页。

所以看到 `302` 就说明一切正常。浏览器会自动跟随这个跳转，然后显示 onboard 页面。

如果你想让 curl 也跟随跳转，加上 `-L`：

```bash
curl -s -L -o /dev/null -w '%{http_code}\n' --max-time 10 \
  'http://106.15.189.47:8090/?token=你的Token'
```

这条命令会返回 `200`。

#### 用浏览器正式访问

验证返回 `302` 或 `200` 后，在任何地方打开浏览器，输入：

```
http://106.15.189.47:8090?token=你的Token
```

如果一切正常，浏览器会自动设置 cookie 并进入 onboard 页面。

---

## 5. 完整脚本汇总

### 5.1 服务器端：转发脚本 `~/forward_onboard.py`

见步骤 2。

### 5.2 一键在服务器上部署并启动

在本地终端执行下面这条命令（会 SSH 到服务器并完成所有操作）：

```bash
ssh aliyun "cat > /home/jyl/forward_onboard.py << 'EOF'
#!/usr/bin/env python3
import asyncio

LOCAL_HOST = '0.0.0.0'
LOCAL_PORT = 8090
REMOTE_HOST = '127.0.0.1'
REMOTE_PORT = 18090

async def forward(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def handle(local_reader, local_writer):
    peer = local_writer.get_extra_info('peername')
    print(f'Connection from {peer}', flush=True)
    try:
        remote_reader, remote_writer = await asyncio.open_connection(REMOTE_HOST, REMOTE_PORT)
        await asyncio.gather(
            forward(local_reader, remote_writer),
            forward(remote_reader, local_writer)
        )
    except Exception as e:
        print(f'Error handling {peer}: {e}', flush=True)
    finally:
        local_writer.close()

async def main():
    server = await asyncio.start_server(handle, LOCAL_HOST, LOCAL_PORT)
    addr = server.sockets[0].getsockname()
    print(f'Forwarding {LOCAL_HOST}:{LOCAL_PORT} -> {REMOTE_HOST}:{REMOTE_PORT}', flush=True)
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
EOF
chmod +x /home/jyl/forward_onboard.py
nohup python3 /home/jyl/forward_onboard.py > /tmp/forward_onboard.log 2>&1 &"
```

> 注意：把 `/home/jyl/` 换成你服务器上的实际用户主目录。

### 5.3 本地：一键建立反向隧道

```bash
ssh -f -N -R 127.0.0.1:18090:localhost:8090 aliyun
```

---

## 6. 日常维护

### 6.1 查看转发脚本是否在运行

```bash
ssh aliyun "ps aux | grep forward_onboard | grep -v grep"
```

### 6.2 查看转发日志

```bash
ssh aliyun "tail -f /tmp/forward_onboard.log"
```

### 6.3 停止转发脚本

```bash
ssh aliyun "pgrep -f 'python3 /home/jyl/forward_onboard.py' | xargs -r kill"
```

### 6.4 停止 SSH 反向隧道

在本地执行：

```bash
pkill -f 'ssh -f -N -R 127.0.0.1:18090:localhost:8090 aliyun'
```

或者找到进程 ID 后 `kill`：

```bash
ps aux | grep 'ssh -f -N -R' | grep -v grep
# kill <PID>
```

### 6.5 让连接更稳定：使用 autossh

如果网络不稳定，SSH 隧道会断。可以安装 `autossh` 自动重连。

在 macOS 上安装：

```bash
brew install autossh
```

然后用 autossh 代替 ssh：

```bash
autossh -M 0 -f -N -R 127.0.0.1:18090:localhost:8090 aliyun
```

`-M 0` 表示不单独开监控端口，依赖 SSH 内置的 `ServerAliveInterval` 心跳。你的 `~/.ssh/config` 里已经配置了 `ServerAliveInterval 30`，所以会自动检测断线并重连。

---

## 7. 常见问题

### Q1：浏览器访问 `http://106.15.189.47:8090` 提示无法连接

请按这个顺序排查，每步都给出命令，直接复制执行即可：

#### 第 1 步：确认本地 onboard 在运行

```bash
lsof -i :8090
```

如果没有输出，说明 onboard 停了，重新启动：

```bash
wolo onboard run
# 或 solo onboard run
```

#### 第 2 步：确认本地 SSH 反向隧道在运行

```bash
ps aux | grep 'ssh -f -N -R' | grep -v grep
```

如果没有输出，重新建立隧道：

```bash
ssh -f -N -R 127.0.0.1:18090:localhost:8090 aliyun
```

#### 第 3 步：确认服务器转发脚本在运行

```bash
ssh aliyun "ps aux | grep 'python3 /home/jyl/forward_onboard.py' | grep -v grep"
```

如果没有输出，重新启动：

```bash
ssh aliyun "nohup python3 /home/jyl/forward_onboard.py > /tmp/forward_onboard.log 2>&1 &"
```

#### 第 4 步：确认服务器端口在监听

```bash
ssh aliyun "ss -tlnp | grep -E '8090|18090'"
```

你应该同时看到两个监听：

```
LISTEN  0  100  0.0.0.0:8090    users:(("python3",pid=...,fd=6))
LISTEN  0  128  127.0.0.1:18090
```

#### 第 5 步：确认服务器本机访问正常

```bash
ssh aliyun "curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://127.0.0.1:8090"
```

如果返回 `401`，说明本地 → SSH 隧道 → 服务器转发 → onboard 整个链路是通的。

#### 第 6 步：判断是不是安全组问题

在服务器上执行：

```bash
ssh aliyun "curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://106.15.189.47:8090"
```

把 `106.15.189.47` 换成你的阿里云 IP。

- 如果返回 `401`：安全组已放行，问题可能在你的本地网络或浏览器。
- 如果返回 `000` 或超时：**100% 是安全组没放行**，请回到步骤 5 检查安全组规则。

> 这个判断方法非常可靠，因为如果连服务器自己访问自己的公网 IP 都超时，那一定是阿里云层面的入站规则拦截了。

### Q2：访问后页面显示 401 Unauthorized

这是正常的 onboard Token Gate 保护机制。请在 URL 后面加上 `?token=你的Token`，或者先在浏览器打开 `http://IP:8090?token=你的Token` 完成认证。

### Q3：curl 返回 302，这是成功还是失败？

**是成功。**

onboard 收到正确的 token 后，会：

1. 给你设置一个 session cookie；
2. 发送 `302 Found` 重定向响应，让你跳到没有 token 参数的主页。

所以 `302` 表示认证通过、链路完全正常。浏览器会自动跟随这个跳转，最终显示 onboard 页面。

如果你用 curl 想看到最终状态码，加上 `-L` 参数：

```bash
curl -s -L -o /dev/null -w '%{http_code}\n' --max-time 10 \
  'http://106.15.189.47:8090/?token=你的Token'
```

这条命令会返回 `200`。

### Q4：Token 忘记了怎么办

```bash
cat ~/.onboard/secret
```

### Q5：服务器重启后怎么办

转发脚本不会自动启动。需要重新 SSH 登录服务器并执行：

```bash
nohup python3 ~/forward_onboard.py > /tmp/forward_onboard.log 2>&1 &
```

如果想开机自启，可以用 `systemd --user` 或 `crontab` 的 `@reboot`。这里不再展开。

### Q6：有没有更简单的方法？

如果你或服务器的管理员有 sudo 权限，最简单的方法是：

1. 修改服务器 `/etc/ssh/sshd_config`，添加或修改：

```ini
GatewayPorts yes
```

2. 重启 sshd：

```bash
sudo systemctl restart sshd
```

3. 本地只需要一条命令：

```bash
ssh -f -N -R 0.0.0.0:8090:localhost:8090 aliyun
```

这样就不需要 Python 转发脚本了，链路更短：

```
浏览器 → 阿里云 0.0.0.0:8090 → SSH 反向隧道 → 本地 onboard
```

---

## 8. 安全提醒

1. **Token 是访问凭证**，不要把它发到公开频道。
2. **安全组授权对象**尽量写成你自己的公网 IP，而不是 `0.0.0.0/0`。
3. **用完即关**：不需要远程访问时，停止 SSH 隧道和服务器转发脚本，减少暴露面。
4. **考虑 HTTPS**：如果数据比较敏感，建议在阿里云服务器上部署 Nginx/Caddy，并配置 HTTPS 证书和 Basic Auth，不要直接暴露 HTTP。

---

## 9. 总结

| 步骤 | 做了什么 | 命令/文件 |
|------|---------|----------|
| 1 | 本地启动 onboard | `wolo onboard run` / `solo onboard run` |
| 2 | 服务器创建转发脚本 | `~/forward_onboard.py` |
| 3 | 启动转发脚本 | `nohup python3 ~/forward_onboard.py ... &` |
| 4 | 本地建立反向隧道 | `ssh -f -N -R 127.0.0.1:18090:localhost:8090 aliyun` |
| 5 | 阿里云安全组放行 8090 | 阿里云控制台 |
| 6 | 获取 Token | `cat ~/.onboard/secret` |
| 7 | 验证并正式访问 | `curl ...` / 浏览器打开 `http://IP:8090?token=...` |

完成以上步骤后，你就拥有了一个可以从任何有互联网的地方访问的 onboard 页面。
