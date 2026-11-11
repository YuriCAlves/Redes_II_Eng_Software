
PROJECT_DIR="/home/yuri/Documentos/console_n2-20261104T141726Z-1-001/console_n2"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV="$BACKEND_DIR/.venv"
SSH_USER="yuri"
SSH_KEY="/home/$SSH_USER/.ssh/id_ed25519" 

FTP_PASS="yuri"               
IMAP_PASS_ALUNO="Yuri@1234"       
IMAP_PASS_SUPORTE="Yuri@1234"   


rm -rf "$PROJECT_DIR/app"

mkdir -p "$BACKEND_DIR/app/routers"
mkdir -p "$FRONTEND_DIR/src/pages"
cp "$PROJECT_DIR/cp3_files_router.py"  "$BACKEND_DIR/app/routers/files.py"
cp "$PROJECT_DIR/cp4_email_router.py"  "$BACKEND_DIR/app/routers/email.py"
cp "$PROJECT_DIR/cp5_ssh_router.py"    "$BACKEND_DIR/app/routers/ssh.py"
cp "$PROJECT_DIR/cp5_hostname_model.py" "$BACKEND_DIR/app/routers/" 

cp "$PROJECT_DIR/cp3_FilesPage.jsx"    "$FRONTEND_DIR/src/pages/FilesPage.jsx"
cp "$PROJECT_DIR/cp4_EmailPage.jsx"    "$FRONTEND_DIR/src/pages/EmailPage.jsx"
cp "$PROJECT_DIR/cp5_SSHPage.jsx"      "$FRONTEND_DIR/src/pages/SSHPage.jsx"

source "$VENV/bin/activate"
pip install paramiko
pip freeze > "$BACKEND_DIR/requirements.txt"


ssh-keyscan -t ed25519 192.168.100.10 >> "$BACKEND_DIR/known_hosts"


cat >> "$BACKEND_DIR/.env" << EOF

FTP_HOST=192.168.100.10
FTP_PORT=21
FTP_USER=ftpuser
FTP_PASS=$FTP_PASS
MAX_UPLOAD_MB=10
HTTP_BASE=http://site.empresa.test/arquivos
SMTP_HOST=192.168.100.10
SMTP_PORT=25
IMAP_HOST=192.168.100.10
IMAP_PORT=143
IMAP_PASS_ALUNO=$IMAP_PASS_ALUNO
IMAP_PASS_SUPORTE=$IMAP_PASS_SUPORTE
SSH_HOST=192.168.100.10
SSH_PORT=22
SSH_USER=$SSH_USER
SSH_KEY_PATH=$SSH_KEY
SSH_KNOWN_HOSTS=$BACKEND_DIR/known_hosts
EOF


cat >> "$BACKEND_DIR/app/models.py" << 'PYEOF'


from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

class HostnameHistory(Base):
    __tablename__ = "hostname_history"
    id       = Column(Integer, primary_key=True, index=True)
    original = Column(String, nullable=False)
    saved_at = Column(DateTime, server_default=func.now())
PYEOF


cd "$BACKEND_DIR"
python - << 'PYEOF'
from app.database import Base, engine
Base.metadata.create_all(engine)
print(">> Tabelas criadas/confirmadas")
PYEOF


python - << 'PYEOF'
import re, pathlib
main_path = pathlib.Path("app/main.py")
content   = main_path.read_text()
imports = "\nfrom app.routers import files, email, ssh\n"
includes = "\napp.include_router(files.router)\napp.include_router(email.router)\napp.include_router(ssh.router)\n"

if "from app.routers import files" not in content:
    content = re.sub(r'(from app\.\w+ import [^\n]+\n)(?!from app\.)', r'\1' + imports, content, count=1)
if "include_router(files.router)" not in content:
    if "if __name__" in content:
        content = content.replace("if __name__", includes + "\nif __name__")
    else:
        content += includes
main_path.write_text(content)
PYEOF

echo "$SSH_USER ALL=(ALL) NOPASSWD: /usr/bin/hostnamectl" | sudo tee /etc/sudoers.d/console-hostnamectl
sudo chmod 440 /etc/sudoers.d/console-hostnamectl

pkill -f "uvicorn app.main" 2>/dev/null || true
sleep 1
cd "$BACKEND_DIR"
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > uvicorn.log 2>&1 &
sleep 2
echo ">> FastAPI reiniciado e rodando na porta 8000!"
