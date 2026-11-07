# System Administration Commands Rule

对系统管理类命令进行分级管控：危险命令禁止执行，软件安装类命令允许后台无人值守执行。

## 禁止执行的命令（可能导致系统死机或不可逆损坏）

以下命令可能导致系统崩溃、数据丢失或不可逆的系统损坏，**严格禁止执行**：

### 系统电源与启动
- `shutdown` / `reboot` / `poweroff` - 系统电源控制
- `init 0` / `init 6` / `telinit` - 切换运行级别
- `update-grub` / `grub-install` - 引导加载器配置

### 内核与系统参数
- `sysctl` - 修改内核参数
- `modprobe` / `insmod` / `rmmod` - 加载/卸载内核模块

### 磁盘与文件系统
- `fdisk` / `parted` / `gdisk` - 磁盘分区
- `mkfs` - 创建文件系统（格式化）
- `mount` / `umount` - 文件系统挂载/卸载
- `lvm` 相关命令 - 逻辑卷管理

### 网络与防火墙
- `iptables` / `ip6tables` - 防火墙规则
- `nftables` / `nft` - 防火墙框架
- `ufw` - 简易防火墙
- `firewalld` / `firewall-cmd` - 防火墙守护进程

### 权限与用户管理
- `useradd` / `userdel` / `usermod` - 用户管理
- `groupadd` / `groupdel` / `groupmod` - 用户组管理
- `passwd` - 修改其他用户密码
- `chown` / `chmod` 作用于系统文件
- `visudo` / 编辑 `/etc/sudoers`
- `chroot` - 切换根目录

### 权限提升
- `sudo` - 提权执行（除下方例外情况）
- `su` - 切换用户

## 允许后台执行的命令（软件安装与依赖管理）

以下命令用于安装软件或管理依赖，**允许执行**，但必须遵守以下规则：

### 执行要求

1. **无人值守模式**：必须添加自动确认参数，避免交互式提示阻塞执行
2. **前台/后台执行策略**：
   - **默认前台执行**：安装完成后再继续后续操作
   - **后台执行条件**：仅当安装的软件包**不被后续操作依赖**时，才可使用 subagent 或 `run_in_background` 方式后台执行。即：后续操作不需要使用该软件包，安装与后续任务之间没有依赖关系
   - **判断原则**：如果不确定后续是否依赖该软件包，按前台执行处理
3. **结果检查**：命令完成后检查退出状态，确认安装成功

### 包管理器及其无人值守参数

| 包管理器 | 无人值守参数 | 示例 |
|---------|------------|------|
| `apt` / `apt-get` | `-y` | `apt-get install -y curl` |
| `dpkg` | `--force-confdef --force-confold`（配置文件冲突时） | `dpkg --configure -a` |
| `yum` | `-y` | `yum install -y gcc` |
| `dnf` | `-y` | `dnf install -y python3` |
| `pacman` | `--noconfirm` | `pacman -S --noconfirm vim` |
| `zypper` | `-n` | `zypper -n install git` |

### 额外环境变量

安装过程中应设置以下环境变量以确保完全无人值守：

```bash
export DEBIAN_FRONTEND=noninteractive
```

### 示例：后台安装软件包

```bash
# 设置非交互模式并安装
DEBIAN_FRONTEND=noninteractive apt-get install -y <package-name>
```

### 服务管理命令

`systemctl` 和 `service` 命令允许在以下场景中使用：
- 启动/停止/重启与当前开发任务相关的服务（如数据库、Web 服务器等）
- 查看服务状态（`systemctl status`、`service --status-all`）

禁止用于：
- 禁用系统关键服务（如 networking、sshd 等）
- 修改服务的 enable/disable 状态（开机自启）

## 允许使用的远程管理命令

### SSH 连接

允许使用 `ssh` 命令连接外部服务器进行管理和业务部署，包括但不限于：

- `ssh user@host` — 连接远程服务器
- `ssh -p <port> user@host` — 指定端口连接
- `scp` / `rsync` — 通过 SSH 进行文件传输
- 在远程服务器上执行部署、运维相关操作

**注意**：
- 禁止通过 SSH 隧道（`ssh -R`、`ssh -L`）在当前环境搭建内网穿透或中转服务，详见 `guardrail.md`
- SSH 私钥文件的内容不得在聊天回复中展示

## 例外情况

如果禁止执行的命令确实是任务必需的，请通知用户并请他们手动执行。
