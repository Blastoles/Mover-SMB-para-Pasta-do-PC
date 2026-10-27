import os
import sys
import json
import uuid
import queue
import time
import ctypes
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import winreg
from datetime import datetime

# Import the monitor module
from monitor import ScanMonitor

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
LOG_FILE = os.path.join(APP_DIR, "printer_scan_mover.log")

# Try to import system tray libraries
TRAY_AVAILABLE = False
try:
    from PIL import Image, ImageDraw
    import pystray
    TRAY_AVAILABLE = True
except ImportError:
    pass


def is_service_running():
    """Checks if the headless background service is running via Global Mutex."""
    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    m = kernel32.OpenMutexW(SYNCHRONIZE, False, "Global\\PrinterScanMover_Service")
    if m:
        kernel32.CloseHandle(m)
        return True
    return False


def focus_existing_gui():
    """If another GUI window is already open in this desktop session, brings it to front and returns True."""
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "Monitor de Escaneamento Multimpressoras")
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # 9 = SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    return False


def run_headless_service():
    """Runs pure background worker without GUI for Windows Task Scheduler / boot."""
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "Global\\PrinterScanMover_Service")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        print("Serviço PrinterScanMover já está em execução.")
        sys.exit(0)

    def get_service_rules():
        if not os.path.exists(CONFIG_FILE):
            return []
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("rules", [])
        except Exception:
            return []

    def log_to_file(msg):
        print(msg)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    log_to_file(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] Serviço de segundo plano iniciado no boot do Windows.")

    monitor = ScanMonitor(config_loader_callback=get_service_rules, log_callback=log_to_file)
    monitor.start()

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        monitor.stop()
        if mutex:
            kernel32.CloseHandle(mutex)


class PrinterScanMoverApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Monitor de Escaneamento Multimpressoras")
        self.root.geometry("950x660")
        self.root.minsize(850, 560)
        
        # Set window icon if exists
        icon_name = "printer.ico"
        self.icon_path = os.path.join(APP_DIR, icon_name)
        if os.path.exists(self.icon_path):
            try:
                self.root.iconbitmap(self.icon_path)
            except Exception:
                pass
        
        # UI Colors (Modern Dark Theme)
        self.bg_color = "#1e1e1e"
        self.card_color = "#2d2d2d"
        self.text_color = "#ffffff"
        self.text_muted = "#aaaaaa"
        self.accent_color = "#1a73e8" # Blue
        self.accent_hover = "#155cb4"
        self.success_color = "#2ecc71" # Green
        self.warning_color = "#f1c40f" # Yellow
        self.danger_color = "#e74c3c" # Red
        self.border_color = "#3d3d3d"
        
        self.root.configure(bg=self.bg_color)
        
        # Thread safety queue for log handling
        self.log_queue = queue.Queue()
        self.log_file_offset = 0
        
        # Check if Background Service is already running on the server
        self.service_mode = is_service_running()
        
        # Load initial configuration
        self.config = self.load_config()
        
        # Create style
        self.setup_styles()
        
        # Setup widgets
        self.build_ui()
        
        # Initialize Monitor Engine (for standalone mode)
        self.monitor = ScanMonitor(
            config_loader_callback=self.get_active_rules,
            log_callback=self.log_callback
        )
        
        # Start queue reader
        self.process_queue_loop()
        
        if self.service_mode:
            self.status_indicator.configure(
                text="EXECUTANDO (SERVIÇO)",
                bg="#1b5e20",
                fg=self.success_color
            )
            self.btn_toggle_monitor.configure(
                text="Parar Serviço",
                bg=self.warning_color,
                fg="black"
            )
            self.load_recent_service_logs()
            self.tail_service_log()
        else:
            # Auto-start monitor if configured and rules active
            self.check_auto_start()

        # Handle window closing to minimize to tray or ask
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)
        self.tray_icon = None

    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")
        
        # Style treeview
        self.style.configure(
            "Treeview",
            background=self.card_color,
            fieldbackground=self.card_color,
            foreground=self.text_color,
            rowheight=28,
            borderwidth=0,
            font=("Segoe UI", 9)
        )
        self.style.configure(
            "Treeview.Heading",
            background=self.border_color,
            foreground=self.text_color,
            relief="flat",
            font=("Segoe UI", 9, "bold")
        )
        self.style.map(
            "Treeview",
            background=[("selected", self.accent_color)],
            foreground=[("selected", "white")]
        )
        
        # Style scrollbar
        self.style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background=self.border_color,
            troughcolor=self.bg_color,
            bordercolor=self.bg_color,
            arrowcolor=self.text_color
        )

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return {"rules": [], "auto_start_monitor": True}
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "rules" not in data:
                    data["rules"] = []
                if "auto_start_monitor" not in data:
                    data["auto_start_monitor"] = True
                return data
        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao ler arquivo de configuração: {e}")
            return {"rules": [], "auto_start_monitor": True}

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log_callback(f"[ERROR] Falha ao salvar configurações em arquivo: {e}")

    def get_active_rules(self):
        """Used by the Monitor Engine to read rules list."""
        return self.config.get("rules", [])

    def log_callback(self, formatted_message):
        """Callback used by monitor engine to output logs."""
        self.log_queue.put(formatted_message)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(formatted_message + "\n")
        except Exception:
            pass

    def load_recent_service_logs(self):
        """Loads the most recent log lines from service.log when opening the UI."""
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    for line in lines[-60:]:
                        self.insert_log_to_ui(line.rstrip("\r\n"))
                    self.log_file_offset = f.tell()
            except Exception:
                pass

    def tail_service_log(self):
        """Periodically reads new entries from service.log so the user sees live activity."""
        if self.service_mode and os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self.log_file_offset)
                    new_lines = f.readlines()
                    self.log_file_offset = f.tell()
                    for line in new_lines:
                        line_str = line.rstrip("\r\n")
                        if line_str:
                            self.insert_log_to_ui(line_str)
            except Exception:
                pass
        self.root.after(1000, self.tail_service_log)

    def process_queue_loop(self):
        """Reads log messages from queue and displays in UI."""
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.insert_log_to_ui(msg)
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue_loop)

    def insert_log_to_ui(self, msg):
        self.log_text.configure(state="normal")
        
        tag = "INFO"
        if "[WARN]" in msg:
            tag = "WARN"
        elif "[ERROR]" in msg:
            tag = "ERROR"
        
        self.log_text.insert(tk.END, msg + "\n", tag)
        
        # Keep logs under 1000 lines
        total_lines = int(self.log_text.index('end-1c').split('.')[0])
        if total_lines > 1000:
            self.log_text.delete("1.0", "200.0")
            
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def build_ui(self):
        # Header Panel
        header_frame = tk.Frame(self.root, bg=self.card_color, height=60)
        header_frame.pack(fill=tk.X, side=tk.TOP)
        header_frame.pack_propagate(False)
        
        header_label = tk.Label(
            header_frame, 
            text="Monitor de Escaneamento Multimpressoras", 
            font=("Segoe UI", 14, "bold"), 
            bg=self.card_color, 
            fg=self.text_color
        )
        header_label.pack(side=tk.LEFT, padx=15, pady=15)
        
        # Top-right service status
        self.status_indicator = tk.Label(
            header_frame,
            text="INATIVO",
            font=("Segoe UI", 10, "bold"),
            bg="#3e2723",
            fg=self.warning_color,
            padx=10,
            pady=4
        )
        self.status_indicator.pack(side=tk.RIGHT, padx=15, pady=15)

        # Main Layout
        main_content = tk.Frame(self.root, bg=self.bg_color)
        main_content.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        # Rule List frame
        rules_frame = tk.LabelFrame(
            main_content, 
            text=" Configurações de Impressoras e Pastas ", 
            font=("Segoe UI", 10, "bold"), 
            bg=self.bg_color, 
            fg=self.text_color,
            bd=1,
            relief="solid"
        )
        rules_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Table + buttons container
        table_container = tk.Frame(rules_frame, bg=self.bg_color)
        table_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Buttons side panel
        btn_panel = tk.Frame(table_container, bg=self.bg_color)
        btn_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

        self.btn_add = tk.Button(
            btn_panel, text="Adicionar Regra", bg=self.accent_color, fg="white", 
            relief="flat", bd=0, activebackground=self.accent_hover, activeforeground="white",
            font=("Segoe UI", 9, "bold"), width=15, height=2, command=self.add_rule_dialog
        )
        self.btn_add.pack(anchor=tk.N, pady=2)

        self.btn_edit = tk.Button(
            btn_panel, text="Editar Regra", bg=self.card_color, fg="white", 
            relief="flat", bd=0, activebackground=self.border_color, activeforeground="white",
            font=("Segoe UI", 9, "bold"), width=15, height=2, command=self.edit_rule_dialog
        )
        self.btn_edit.pack(anchor=tk.N, pady=2)

        self.btn_delete = tk.Button(
            btn_panel, text="Excluir Regra", bg=self.danger_color, fg="white", 
            relief="flat", bd=0, activebackground="#c0392b", activeforeground="white",
            font=("Segoe UI", 9, "bold"), width=15, height=2, command=self.delete_rule
        )
        self.btn_delete.pack(anchor=tk.N, pady=2)
        
        # Spacer
        tk.Label(btn_panel, bg=self.bg_color).pack(fill=tk.Y, expand=True)

        self.btn_toggle_monitor = tk.Button(
            btn_panel, text="Iniciar Monitor", bg=self.success_color, fg="black", 
            relief="flat", bd=0, activebackground="#27ae60", activeforeground="black",
            font=("Segoe UI", 10, "bold"), width=15, height=2, command=self.toggle_monitor
        )
        self.btn_toggle_monitor.pack(anchor=tk.S, pady=2)

        # Table scrollbar
        scrollbar = ttk.Scrollbar(table_container, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Treeview definition
        self.tree = ttk.Treeview(
            table_container, 
            columns=("id", "name", "source", "destination", "interval", "cleanup", "status"),
            show="headings", 
            yscrollcommand=scrollbar.set
        )
        scrollbar.config(command=self.tree.yview)
        
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Nome da Regra")
        self.tree.heading("source", text="Pasta de Origem (Impressora/IP)")
        self.tree.heading("destination", text="Pasta de Destino (PC)")
        self.tree.heading("interval", text="Intervalo (s)")
        self.tree.heading("cleanup", text="Limpeza")
        self.tree.heading("status", text="Ativo?")

        self.tree.column("id", width=0, stretch=tk.NO)
        self.tree.column("name", width=150, anchor=tk.W)
        self.tree.column("source", width=220, anchor=tk.W)
        self.tree.column("destination", width=180, anchor=tk.W)
        self.tree.column("interval", width=70, anchor=tk.CENTER)
        self.tree.column("cleanup", width=80, anchor=tk.CENTER)
        self.tree.column("status", width=60, anchor=tk.CENTER)
        
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.populate_table()

        # Logs panel
        log_frame = tk.LabelFrame(
            main_content, 
            text=" Registro de Atividades (Logs em tempo real) ", 
            font=("Segoe UI", 10, "bold"), 
            bg=self.bg_color, 
            fg=self.text_color,
            bd=1,
            relief="solid"
        )
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        # Toolbar inside logs
        log_toolbar = tk.Frame(log_frame, bg=self.bg_color)
        log_toolbar.pack(fill=tk.X, side=tk.TOP, padx=10, pady=(5, 0))
        
        btn_clear_log = tk.Button(
            log_toolbar, text="Limpar Logs", bg=self.card_color, fg=self.text_color,
            relief="flat", bd=0, activebackground=self.border_color, activeforeground="white",
            font=("Segoe UI", 8, "bold"), command=self.clear_logs, padx=8, pady=2
        )
        btn_clear_log.pack(side=tk.RIGHT)
        
        # Checkbox 1: Task Scheduler boot service (NO LOGIN REQUIRED)
        self.boot_service_var = tk.BooleanVar(value=self.check_task_scheduler())
        chk_service = tk.Checkbutton(
            log_toolbar, text="Subir no Boot (Sem Login / Agendador)", variable=self.boot_service_var,
            bg=self.bg_color, fg=self.success_color, selectcolor=self.bg_color, activebackground=self.bg_color,
            activeforeground=self.success_color, font=("Segoe UI", 9, "bold"), command=self.toggle_boot_service
        )
        chk_service.pack(side=tk.LEFT, padx=(0, 15))

        # Checkbox 2: Start with Windows Registry (with user login)
        self.start_with_windows_var = tk.BooleanVar(value=self.check_startup_registry())
        chk_startup = tk.Checkbutton(
            log_toolbar, text="Iniciar ao Logar no Windows", variable=self.start_with_windows_var,
            bg=self.bg_color, fg=self.text_color, selectcolor=self.bg_color, activebackground=self.bg_color,
            activeforeground=self.text_color, font=("Segoe UI", 9), command=self.toggle_startup
        )
        chk_startup.pack(side=tk.LEFT, padx=(0, 15))

        # Checkbox 3: Auto-start monitor on app open
        self.auto_start_var = tk.BooleanVar(value=self.config.get("auto_start_monitor", True))
        chk_autostart = tk.Checkbutton(
            log_toolbar, text="Auto-iniciar ao abrir", variable=self.auto_start_var,
            bg=self.bg_color, fg=self.text_color, selectcolor=self.bg_color, activebackground=self.bg_color,
            activeforeground=self.text_color, font=("Segoe UI", 9), command=self.toggle_auto_start_setting
        )
        chk_autostart.pack(side=tk.LEFT)

        # Log content text field
        log_container = tk.Frame(log_frame, bg=self.bg_color)
        log_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        log_scroll = ttk.Scrollbar(log_container, orient="vertical")
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(
            log_container, 
            bg="#121212", 
            fg="#cccccc", 
            insertbackground="white", 
            state="disabled", 
            font=("Consolas", 10),
            relief="flat",
            yscrollcommand=log_scroll.set
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        log_scroll.config(command=self.log_text.yview)

        # Configure log tags
        self.log_text.tag_config("INFO", foreground="#2ecc71")
        self.log_text.tag_config("WARN", foreground="#f39c12")
        self.log_text.tag_config("ERROR", foreground="#e74c3c")

    def populate_table(self):
        for child in self.tree.get_children():
            self.tree.delete(child)
            
        for rule in self.config.get("rules", []):
            status = "Sim" if rule.get("enabled", False) else "Não"
            cleanup_days = rule.get("cleanup_days", 0)
            cleanup_str = f"{cleanup_days} dias" if cleanup_days > 0 else "Desativada"
            self.tree.insert(
                "", 
                tk.END, 
                values=(
                    rule.get("id"),
                    rule.get("name"),
                    rule.get("source"),
                    rule.get("destination"),
                    rule.get("interval", 5),
                    cleanup_str,
                    status
                )
            )

    def clear_logs(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def toggle_auto_start_setting(self):
        self.config["auto_start_monitor"] = self.auto_start_var.get()
        self.save_config()

    def check_auto_start(self):
        if self.config.get("auto_start_monitor", True) and len(self.config.get("rules", [])) > 0:
            if any(rule.get("enabled", False) for rule in self.config.get("rules", [])):
                self.root.after(500, self.start_monitoring)

    def toggle_monitor(self):
        if self.service_mode:
            confirm = messagebox.askyesno(
                "Parar Serviço", 
                "O serviço de monitoramento está rodando em segundo plano no Windows.\nDeseja finalizá-lo agora?"
            )
            if confirm:
                subprocess.run(["schtasks", "/End", "/TN", "PrinterScanMover"], capture_output=True)
                time.sleep(1)
                self.service_mode = is_service_running()
                if not self.service_mode:
                    self.status_indicator.configure(text="INATIVO", bg="#3e2723", fg=self.warning_color)
                    self.btn_toggle_monitor.configure(text="Iniciar Monitor", bg=self.success_color, fg="black")
                    self.log_callback("[INFO] Serviço em segundo plano finalizado.")
        else:
            if self.monitor.is_running():
                self.stop_monitoring()
            else:
                self.start_monitoring()

    def start_monitoring(self):
        active = [r for r in self.config.get("rules", []) if r.get("enabled", False)]
        if not active:
            messagebox.showwarning("Aviso", "Nenhuma regra ativa para monitorar. Ative ou crie uma regra primeiro.")
            return

        self.monitor.start()
        self.status_indicator.configure(
            text="EXECUTANDO", 
            bg="#1b5e20", 
            fg=self.success_color
        )
        self.btn_toggle_monitor.configure(
            text="Parar Monitor",
            bg=self.warning_color,
            fg="black"
        )

    def stop_monitoring(self):
        self.monitor.stop()
        self.status_indicator.configure(
            text="INATIVO", 
            bg="#3e2723", 
            fg=self.warning_color
        )
        self.btn_toggle_monitor.configure(
            text="Iniciar Monitor",
            bg=self.success_color,
            fg="black"
        )

    # Task Scheduler configuration (Runs on boot with NO USER LOGIN required)
    def check_task_scheduler(self):
        try:
            res = subprocess.run(
                ["schtasks", "/Query", "/TN", "PrinterScanMover"],
                capture_output=True,
                encoding="cp850",
                errors="replace"
            )
            return res.returncode == 0
        except Exception:
            return False

    def toggle_boot_service(self):
        enabled = self.boot_service_var.get()
        task_name = "PrinterScanMover"
        
        if getattr(sys, 'frozen', False):
            exe_cmd = f'"{sys.executable}" --service'
        else:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            exe_cmd = f'"{pythonw}" "{script_path}" --service'

        if enabled:
            try:
                res = subprocess.run(
                    ["schtasks", "/Create", "/TN", task_name, "/TR", exe_cmd, "/SC", "ONSTART", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"],
                    capture_output=True,
                    encoding="cp850",
                    errors="replace"
                )
                if res.returncode == 0:
                    self.log_callback("[INFO] Inicialização no Boot (sem login) ativada com sucesso no Agendador de Tarefas.")
                    messagebox.showinfo(
                        "Sucesso", 
                        "O monitor agora iniciará automaticamente com o boot do servidor, mesmo sem nenhum usuário conectado."
                    )
                else:
                    err_msg = res.stderr.strip() or res.stdout.strip()
                    messagebox.showerror(
                        "Permissão Necessária", 
                        f"Falha ao criar tarefa no agendador do Windows (requer permissão de Administrador):\n{err_msg}"
                    )
                    self.boot_service_var.set(False)
            except Exception as e:
                messagebox.showerror("Erro", f"Erro ao executar schtasks: {e}")
                self.boot_service_var.set(False)
        else:
            try:
                res = subprocess.run(
                    ["schtasks", "/Delete", "/TN", task_name, "/F"],
                    capture_output=True,
                    encoding="cp850",
                    errors="replace"
                )
                if res.returncode == 0:
                    self.log_callback("[INFO] Inicialização no Boot desativada no Agendador de Tarefas.")
                else:
                    self.log_callback(f"[WARN] Erro ao desativar tarefa no Agendador: {res.stderr.strip()}")
            except Exception as e:
                self.log_callback(f"[ERROR] Erro ao remover tarefa: {e}")

    # Startup control via Windows Registry (with user logon)
    def check_startup_registry(self):
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "PrinterScanMover"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, app_name)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def toggle_startup(self):
        enabled = self.start_with_windows_var.get()
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "PrinterScanMover"
        
        if getattr(sys, 'frozen', False):
            cmd = f'"{sys.executable}" --minimized'
        else:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = "pythonw.exe"
            script_path = os.path.abspath(sys.argv[0])
            cmd = f'"{pythonw}" "{script_path}" --minimized'

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE)
            if enabled:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
                self.log_callback("[INFO] Inicialização ao logar ativada com sucesso.")
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                    self.log_callback("[INFO] Inicialização ao logar desativada.")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível ajustar registro de inicialização: {e}")
            self.start_with_windows_var.set(not enabled)

    # Add/Edit rules dialog
    def add_rule_dialog(self):
        self.rule_form_dialog(None)

    def edit_rule_dialog(self):
        selected_item = self.tree.selection()
        if not selected_item:
            messagebox.showwarning("Aviso", "Selecione uma regra na tabela para editar.")
            return
        
        rule_id = self.tree.item(selected_item)["values"][0]
        rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
        if rule:
            self.rule_form_dialog(rule)

    def rule_form_dialog(self, rule_data=None):
        title = "Editar Regra" if rule_data else "Adicionar Nova Regra"
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("550x485")
        dialog.configure(bg=self.bg_color)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        lbl_style = {"bg": self.bg_color, "fg": self.text_color, "font": ("Segoe UI", 9, "bold")}
        entry_style = {"bg": "#3d3d3d", "fg": "white", "insertbackground": "white", "relief": "flat", "bd": 1, "font": ("Segoe UI", 10)}

        form_frame = tk.Frame(dialog, bg=self.bg_color, padx=20, pady=20)
        form_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(form_frame, text=title, font=("Segoe UI", 12, "bold"), bg=self.bg_color, fg=self.accent_color).pack(anchor=tk.W, pady=(0, 15))

        # Rule Name
        tk.Label(form_frame, text="Nome da Regra (ex: Impressora Recepção):", **lbl_style).pack(anchor=tk.W, pady=(5, 2))
        entry_name = tk.Entry(form_frame, **entry_style)
        entry_name.pack(fill=tk.X, pady=(0, 10))
        if rule_data:
            entry_name.insert(0, rule_data.get("name", ""))

        # Source Path
        tk.Label(form_frame, text="Pasta de Origem (Scanner/Rede IP ou Local):", **lbl_style).pack(anchor=tk.W, pady=(5, 2))
        src_container = tk.Frame(form_frame, bg=self.bg_color)
        src_container.pack(fill=tk.X, pady=(0, 10))
        
        entry_src = tk.Entry(src_container, **entry_style)
        entry_src.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if rule_data:
            entry_src.insert(0, rule_data.get("source", ""))

        def browse_src():
            selected = filedialog.askdirectory(title="Selecione a Pasta de Origem (Scan)")
            if selected:
                entry_src.delete(0, tk.END)
                entry_src.insert(0, os.path.normpath(selected))

        btn_browse_src = tk.Button(
            src_container, text="Procurar...", bg=self.card_color, fg="white",
            relief="flat", bd=0, activebackground=self.border_color, activeforeground="white",
            command=browse_src, padx=10
        )
        btn_browse_src.pack(side=tk.RIGHT, padx=(10, 0))

        # Destination Path
        tk.Label(form_frame, text="Pasta de Destino (Pasta no seu PC):", **lbl_style).pack(anchor=tk.W, pady=(5, 2))
        dest_container = tk.Frame(form_frame, bg=self.bg_color)
        dest_container.pack(fill=tk.X, pady=(0, 10))
        
        entry_dest = tk.Entry(dest_container, **entry_style)
        entry_dest.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if rule_data:
            entry_dest.insert(0, rule_data.get("destination", ""))

        def browse_dest():
            selected = filedialog.askdirectory(title="Selecione a Pasta de Destino no PC")
            if selected:
                entry_dest.delete(0, tk.END)
                entry_dest.insert(0, os.path.normpath(selected))

        btn_browse_dest = tk.Button(
            dest_container, text="Procurar...", bg=self.card_color, fg="white",
            relief="flat", bd=0, activebackground=self.border_color, activeforeground="white",
            command=browse_dest, padx=10
        )
        btn_browse_dest.pack(side=tk.RIGHT, padx=(10, 0))

        # Horizontal group for Interval & Enabled
        group_frame = tk.Frame(form_frame, bg=self.bg_color)
        group_frame.pack(fill=tk.X, pady=5)

        # Interval
        int_frame = tk.Frame(group_frame, bg=self.bg_color)
        int_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(int_frame, text="Intervalo de Checagem (segundos):", **lbl_style).pack(anchor=tk.W, pady=(0, 2))
        entry_interval = tk.Entry(int_frame, **entry_style, width=10)
        entry_interval.pack(anchor=tk.W)
        entry_interval.insert(0, str(rule_data.get("interval", 5)) if rule_data else "5")

        # Enabled Checkbox
        enabled_var = tk.BooleanVar(value=rule_data.get("enabled", True) if rule_data else True)
        chk_enabled = tk.Checkbutton(
            group_frame, text="Regra Ativa", variable=enabled_var,
            bg=self.bg_color, fg=self.text_color, selectcolor=self.bg_color,
            activebackground=self.bg_color, activeforeground=self.text_color,
            font=("Segoe UI", 9, "bold")
        )
        chk_enabled.pack(side=tk.RIGHT, padx=10, pady=(15, 0))

        # Cleanup Days
        cleanup_group = tk.Frame(form_frame, bg=self.bg_color)
        cleanup_group.pack(fill=tk.X, pady=10)
        
        cleanup_frame = tk.Frame(cleanup_group, bg=self.bg_color)
        cleanup_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(cleanup_frame, text="Autolimpeza de Destino (dias, 0 para desativar):", **lbl_style).pack(anchor=tk.W, pady=(0, 2))
        entry_cleanup = tk.Entry(cleanup_frame, **entry_style, width=10)
        entry_cleanup.pack(anchor=tk.W)
        entry_cleanup.insert(0, str(rule_data.get("cleanup_days", 0)) if rule_data else "0")

        # Bottom buttons
        actions_frame = tk.Frame(form_frame, bg=self.bg_color)
        actions_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(15, 0))

        def save_rule():
            name = entry_name.get().strip()
            src = entry_src.get().strip()
            dest = entry_dest.get().strip()
            interval_str = entry_interval.get().strip()
            cleanup_str = entry_cleanup.get().strip()
            enabled = enabled_var.get()

            if not name or not src or not dest:
                messagebox.showerror("Erro", "Por favor, preencha todos os campos obrigatórios (Nome, Origem, Destino).", parent=dialog)
                return
            
            try:
                interval = int(interval_str)
                if interval < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Erro", "O intervalo de checagem deve ser um número inteiro maior ou igual a 1.", parent=dialog)
                return

            try:
                cleanup_days = int(cleanup_str)
                if cleanup_days < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Erro", "A autolimpeza deve ser um número de dias inteiro maior ou igual a 0.", parent=dialog)
                return

            if rule_data:
                rule_data["name"] = name
                rule_data["source"] = src
                rule_data["destination"] = dest
                rule_data["interval"] = interval
                rule_data["cleanup_days"] = cleanup_days
                rule_data["enabled"] = enabled
                self.log_callback(f"[INFO] Regra '{name}' atualizada.")
            else:
                new_rule = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "source": src,
                    "destination": dest,
                    "interval": interval,
                    "cleanup_days": cleanup_days,
                    "enabled": enabled
                }
                self.config["rules"].append(new_rule)
                self.log_callback(f"[INFO] Nova regra criada: '{name}'.")

            self.save_config()
            self.populate_table()
            dialog.destroy()

            # In standalone mode, auto-start monitor if enabled and not running
            if not self.service_mode and enabled and not self.monitor.is_running():
                self.start_monitoring()

        btn_save = tk.Button(
            actions_frame, text="Salvar Regra", bg=self.success_color, fg="black",
            relief="flat", bd=0, activebackground="#27ae60", activeforeground="black",
            font=("Segoe UI", 9, "bold"), command=save_rule, width=15, height=2
        )
        btn_save.pack(side=tk.RIGHT, padx=(10, 0))

        btn_cancel = tk.Button(
            actions_frame, text="Cancelar", bg=self.card_color, fg="white",
            relief="flat", bd=0, activebackground=self.border_color, activeforeground="white",
            font=("Segoe UI", 9), command=dialog.destroy, width=10, height=2
        )
        btn_cancel.pack(side=tk.RIGHT)

    def delete_rule(self):
        selected_item = self.tree.selection()
        if not selected_item:
            messagebox.showwarning("Aviso", "Selecione uma regra na tabela para excluir.")
            return

        rule_id = self.tree.item(selected_item)["values"][0]
        rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
        
        if rule:
            confirm = messagebox.askyesno("Confirmar Exclusão", f"Tem certeza que deseja remover a regra '{rule.get('name')}'?")
            if confirm:
                self.config["rules"] = [r for r in self.config["rules"] if r["id"] != rule_id]
                self.save_config()
                self.populate_table()
                self.log_callback(f"[INFO] Regra '{rule.get('name')}' removida.")

    def on_close_window(self):
        """Minimize to tray if available or shutdown gracefully."""
        if TRAY_AVAILABLE:
            self.minimize_to_tray()
        else:
            confirm = messagebox.askyesno(
                "Fechar Aplicativo", 
                "O monitoramento continuará apenas se o serviço de segundo plano estiver ativo.\nDeseja realmente sair da tela?"
            )
            if confirm:
                self.shutdown_app()

    def minimize_to_tray(self):
        if not TRAY_AVAILABLE:
            return
            
        self.root.withdraw()
        
        if not self.tray_icon:
            image = None
            if os.path.exists(self.icon_path):
                try:
                    image = Image.open(self.icon_path)
                except Exception:
                    pass
            
            if not image:
                image = Image.new('RGB', (64, 64), color=(30, 30, 30))
                d = ImageDraw.Draw(image)
                d.rectangle([16, 24, 48, 40], fill=(26, 115, 232), outline=(255, 255, 255))
                d.line([16, 32, 48, 32], fill=(255, 255, 255), width=2)
            
            menu = pystray.Menu(
                pystray.MenuItem("Abrir Painel", self.restore_from_tray, default=True),
                pystray.MenuItem("Sair da Tela", self.shutdown_from_tray)
            )
            
            self.tray_icon = pystray.Icon("PrinterScanMover", image, "Monitor de Escaneamento", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            
        self.log_callback("[INFO] Janela minimizada para a bandeja do sistema.")

    def restore_from_tray(self, icon=None, item=None):
        if self.tray_icon:
            self.root.deiconify()
            self.root.focus_force()

    def shutdown_from_tray(self, icon, item):
        self.tray_icon.stop()
        self.root.after(0, self.shutdown_app)

    def shutdown_app(self):
        if not self.service_mode and self.monitor.is_running():
            self.monitor.stop()
        self.root.destroy()
        sys.exit(0)


if __name__ == "__main__":
    # If launched with "--service" argument (e.g. from Task Scheduler at boot)
    if "--service" in sys.argv:
        run_headless_service()
        sys.exit(0)

    # Single-instance enforcement for GUI: bring existing window to front if already open
    if focus_existing_gui():
        sys.exit(0)

    root = tk.Tk()
    app = PrinterScanMoverApp(root)
    
    if "--minimized" in sys.argv:
        if TRAY_AVAILABLE:
            root.withdraw()
            root.after(1000, app.minimize_to_tray)
        else:
            root.iconify()
            
    root.mainloop()
