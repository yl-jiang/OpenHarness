VENV_BIN ?= .venv/bin
ONBOARD_ARGS ?=
RESTART_CLEAN ?= scripts/restart_clean.sh

.PHONY: all install install-dev test test-cov cov-html lint format typecheck check build clean \
        onboard solo-gw wolo-gw solo wolo stop restart clean-runtime

# 默认执行测试
all: test

# -----------------
# 依赖安装
# -----------------

# 安装开发依赖
install:
	pip install -e ".[dev]"

# 执行开发环境安装脚本
install-dev:
	bash scripts/install_dev.sh

# -----------------
# 测试与覆盖率
# -----------------

# 执行所有测试
test:
	$(VENV_BIN)/pytest

# 执行测试并在终端输出覆盖率报告
test-cov:
	$(VENV_BIN)/pytest --cov=src/openharness --cov=ohmo

# 执行测试并生成 HTML 覆盖率报告
cov-html:
	$(VENV_BIN)/pytest --cov=src/openharness --cov=ohmo --cov-report=html

# -----------------
# 代码质量检查
# -----------------

# 检查代码规范 (使用 ruff)
lint:
	$(VENV_BIN)/ruff check src ohmo tests

# 自动格式化代码 (使用 ruff)
format:
	$(VENV_BIN)/ruff check --fix src ohmo tests
	$(VENV_BIN)/ruff format src ohmo tests

# 类型检查 (使用 mypy)
typecheck:
	$(VENV_BIN)/mypy src ohmo tests

# 运行所有代码质量检查 (格式化、lint、类型检查)
check: format lint typecheck

# -----------------
# 构建与清理
# -----------------

# 构建 Python 安装包 (wheel/sdist)
build:
	python -m build

# -----------------
# 运行时生命周期
# -----------------
#
# 单独启动类 target (solo-gw / wolo-gw / onboard) 只清理本应用的旧进程和
# 守护进程，不会影响其他正在运行的应用，方便在 console 中查看各自的 log。
#
# 组合启动类 target (solo / wolo) 会清理对应 gateway + onboard 的旧进程，
# 然后以 daemon 方式同时启动两者。
#
# stop / restart 保持全量清理的行为。

# 前台启动 onboard（只清理 onboard 旧进程）
onboard:
	@$(RESTART_CLEAN) --only onboard --quiet
	npm --prefix onboard/frontend ci
	npm --prefix onboard/frontend run build
	uv run onboard run $(ONBOARD_ARGS)

# 前台启动 solo gateway（只清理 solo 旧进程和守护进程）
solo-gw:
	@$(RESTART_CLEAN) --only solo --quiet
	uv run solo gateway run

# 前台启动 wolo gateway（只清理 wolo 旧进程和守护进程）
wolo-gw:
	@$(RESTART_CLEAN) --only wolo --quiet
	uv run wolo gateway run

# 一键启动 solo gateway + onboard（先清理两者的旧进程，再 daemon 启动）
solo:
	@$(RESTART_CLEAN) --only solo --only onboard --quiet
	@echo "[solo] starting solo-gw + onboard as daemons..."
	@mkdir -p /tmp/openharness
	@nohup uv run solo gateway run      >/tmp/openharness/solo-gw.log  2>&1 & echo "solo-gw     pid=$$!  log=/tmp/openharness/solo-gw.log"
	@nohup uv run onboard run $(ONBOARD_ARGS) >/tmp/openharness/onboard.log 2>&1 & echo "onboard     pid=$$!  log=/tmp/openharness/onboard.log"
	@echo "[solo] launched. Use 'make stop' to stop them."

# 一键启动 wolo gateway + onboard（先清理两者的旧进程，再 daemon 启动）
wolo:
	@$(RESTART_CLEAN) --only wolo --only onboard --quiet
	@echo "[wolo] starting wolo-gw + onboard as daemons..."
	@mkdir -p /tmp/openharness
	@nohup uv run wolo gateway run      >/tmp/openharness/wolo-gw.log  2>&1 & echo "wolo-gw     pid=$$!  log=/tmp/openharness/wolo-gw.log"
	@nohup uv run onboard run $(ONBOARD_ARGS) >/tmp/openharness/onboard.log 2>&1 & echo "onboard     pid=$$!  log=/tmp/openharness/onboard.log"
	@echo "[wolo] launched. Use 'make stop' to stop them."

# 仅停止所有常驻进程（wolo gateway + solo gateway + onboard + cron 调度器守护进程），不清缓存
stop:
	@$(RESTART_CLEAN) --no-pyc

# 停止所有常驻进程 + 清缓存 + 重启 onboard / solo-gw / wolo-gw（三件套）
# 注意：onboard 不会重建前端；如需重 build 前端请单独跑 `make onboard`
restart:
	@$(RESTART_CLEAN)
	@echo "[restart] starting onboard + solo-gw + wolo-gw as daemons..."
	@mkdir -p /tmp/openharness
	@nohup uv run onboard run $(ONBOARD_ARGS) >/tmp/openharness/onboard.log 2>&1 & echo "onboard     pid=$$!  log=/tmp/openharness/onboard.log"
	@nohup uv run solo gateway run      >/tmp/openharness/solo-gw.log  2>&1 & echo "solo-gw     pid=$$!  log=/tmp/openharness/solo-gw.log"
	@nohup uv run wolo gateway run      >/tmp/openharness/wolo-gw.log  2>&1 & echo "wolo-gw     pid=$$!  log=/tmp/openharness/wolo-gw.log"
	@echo "[restart] all three services launched. Use 'make stop' to stop them."

# 仅清理运行时缓存（__pycache__ / .pytest_cache / .mypy_cache / .ruff_cache），不停进程
clean-runtime:
	@$(RESTART_CLEAN) --no-stop

# 清理缓存和构建产物（传统 clean：不动运行时进程）
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build *.egg-info
