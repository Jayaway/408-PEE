#!/bin/bash
# ============================================
# 408刷题系统 - 阿里云一键部署脚本
# 适用: Ubuntu 20.04+ / CentOS 7+
# ============================================
set -e

APP_DIR="/opt/408_practice"
PYTHON_BIN=$(which python3 2>/dev/null || which python 2>/dev/null)
DOMAIN_OR_IP="47.98.236.130"
PORT=8000

echo "========================================="
echo "  408智能刷题助手 - 服务器部署"
echo "========================================="

# --- 1. 安装系统依赖 ---
echo "[1/6] 安装系统依赖..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip python3-venv nginx
elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip nginx
else
    echo "不支持的包管理器，请手动安装: python3 python3-pip nginx"
    exit 1
fi

# --- 2. 创建应用目录 ---
echo "[2/6] 创建应用目录..."
sudo mkdir -p $APP_DIR
sudo chown $USER:$USER $APP_DIR

# --- 3. 复制项目文件 ---
echo "[3/6] 复制项目文件..."
# 假设 deploy.sh 在项目根目录
cp -r ./* $APP_DIR/ 2>/dev/null || {
    echo "请将 deploy.sh 放在项目根目录 (manage.py 同级) 执行"
    exit 1
}
cd $APP_DIR

# --- 4. 创建虚拟环境并安装依赖 ---
echo "[4/6] 安装 Python 依赖..."
$PYTHON_BIN -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install django>=4.2 gunicorn requests -q

# --- 5. 设置生产模式配置 ---
echo "[5/6] 配置生产环境..."
# 生成 SECRET_KEY
NEW_KEY=$($PYTHON_BIN -c "import secrets; print(secrets.token_urlsafe(50))")

# 更新 settings.py
SETTINGS_FILE="$APP_DIR/practice_408/settings.py"
sed -i "s/DEBUG = True/DEBUG = False/" "$SETTINGS_FILE"
sed -i "s/ALLOWED_HOSTS = \[\]/ALLOWED_HOSTS = ['$DOMAIN_OR_IP', 'localhost', '127.0.0.1']/" "$SETTINGS_FILE"
sed -i "s/SECRET_KEY = '.*'/SECRET_KEY = '$NEW_KEY'/" "$SETTINGS_FILE"

# 添加 STATIC_ROOT
grep -q "STATIC_ROOT" "$SETTINGS_FILE" || echo "
STATIC_ROOT = BASE_DIR / 'staticfiles'" >> "$SETTINGS_FILE"

# --- 6. 数据库迁移 & 静态文件收集 ---
echo "[6/6] 数据库迁移..."
$PYTHON_BIN manage.py migrate --noinput 2>/dev/null || true
$PYTHON_BIN manage.py collectstatic --noinput 2>/dev/null || true

# --- 7. 创建 systemd 服务 ---
echo ""
echo "========================================="
echo "  创建 systemd 服务..."
echo "========================================="

SERVICE_FILE="/etc/systemd/system/408practice.service"
sudo tee $SERVICE_FILE > /dev/null << EOF
[Unit]
Description=408 Practice Assistant
After=network.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/gunicorn practice_408.wsgi:application \\
    --bind 127.0.0.1:$PORT \\
    --workers 2 \\
    --timeout 60 \\
    --access-logfile /var/log/408practice-access.log \\
    --error-logfile /var/log/408practice-error.log
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# --- 8. Nginx 配置 ---
echo ""
echo "========================================="
echo "  配置 Nginx 反向代理..."
echo "========================================="

NGINX_FILE="/etc/nginx/sites-available/408practice"
sudo tee $NGINX_FILE > /dev/null << EOF
server {
    listen 80;
    server_name $DOMAIN_OR_IP;

    client_max_body_size 10M;

    location /static/ {
        alias $APP_DIR/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# 启用站点
sudo ln -sf $NGINX_FILE /etc/nginx/sites-enabled/408practice 2>/dev/null || true
sudo rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

# 测试 nginx 配置
sudo nginx -t

# --- 9. 启动服务 ---
echo ""
echo "========================================="
echo "  启动服务..."
echo "========================================="
sudo systemctl daemon-reload
sudo systemctl enable 408practice
sudo systemctl restart 408practice
sudo systemctl restart nginx

# --- 10. 阿里云安全组提示 ---
echo ""
echo "========================================="
echo "  ✅ 部署完成！"
echo "========================================="
echo ""
echo "  访问地址: http://$DOMAIN_OR_IP/"
echo ""
echo "  ⚠️  别忘了去阿里云控制台开放端口:"
echo "     安全组 → 入方向 → 添加规则"
echo "     端口: 80 (HTTP)"
echo "     授权对象: 0.0.0.0/0"
echo ""
echo "  管理命令:"
echo "    查看状态: sudo systemctl status 408practice"
echo "    重启:     sudo systemctl restart 408practice"
echo "    查看日志: sudo journalctl -u 408practice -f"
echo ""
