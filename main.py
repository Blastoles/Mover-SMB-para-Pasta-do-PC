import os
import sys
import json
import uuid
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import winreg

# Import the monitor module we created
from monitor import ScanMonitor

if getattr(sys, 'frozen', False):
    CONFIG_FILE = os.path.join(os.path.dirname(sys.executable), "config.json")
else:
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Try to import system tray libraries
TRAY_AVAILABLE = False
try:
    from PIL import Image, ImageDraw
    import pystray
    TRAY_AVAILABLE = True
except ImportError:
    pass

class PrinterScanMoverApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Monitor de Escaneamento Multimpressoras")
        self.root.geometry("900x650")
        self.root.minsize(800, 550)
        
        # Set window icon if exists
        icon_name = "printer.ico"
        if getattr(sys, 'frozen', False):
            self.icon_path = os.path.join(os.path.dirname(sys.executable), icon_name)
        else:
            self.icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), icon_name)
            
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
        
        # Load initial configuration
        self.config = self.load_config()
        
        # Create style
        self.setup_styles()
        
        # Setup widgets
        self.build_ui()
        
        # Initialize Monitor Engine
        self.monitor = ScanMonitor(
            config_loader_callback=self.get_active_rules,
            log_callback=self.log_callback
        )
        
        # Start queue reader
        self.process_queue_loop()
        
        # Auto-start monitor if there are active rules
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
        
        # Determine log level to apply custom tags/colors
        tag = "INFO"
        if "[WARN]" in msg:
            tag = "WARN"
        elif "[ERROR]" in msg:
            tag = "ERROR"
        
        self.log_text.insert(tk.END, msg + "\n", tag)
        
        # Keep logs under 1000 lines to avoid memory buildup
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

        # Main Layout split: Config (top half) and Log (bottom half)
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

        # Modern flat buttons
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
        self.tree.column("name", width=160, anchor=tk.W)
        self.tree.column("source", width=220, anchor=tk.W)
        self.tree.column("destination", width=180, anchor=tk.W)
        self.tree.column("interval", width=70, anchor=tk.CENTER)
        self.tree.column("cleanup", width=80, anchor=tk.CENTER)
        self.tree.column("status", width=60, anchor=tk.CENTER)
        
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.populate_table()

        # Logs panel (bottom half)
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
        
        # Checkbox options
        self.start_with_windows_var = tk.BooleanVar(value=self.check_startup_registry())
        chk_startup = tk.Checkbutton(
            log_toolbar, text="Iniciar com o Windows", variable=self.start_with_windows_var,
            bg=self.bg_color, fg=self.text_color, selectcolor=self.bg_color, activebackground=self.bg_color,
            activeforeground=self.text_color, font=("Segoe UI", 9), command=self.toggle_startup
        )
        chk_startup.pack(side=tk.LEFT, padx=(0, 15))

        self.auto_start_var = tk.BooleanVar(value=self.config.get("auto_start_monitor", True))
        chk_autostart = tk.Checkbutton(
            log_toolbar, text="Auto-iniciar monitoramento ao abrir app", variable=self.auto_start_var,
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
        
        # Tray notice if pystray/pillow is missing
        if not TRAY_AVAILABLE:
            tray_notice = tk.Label(
                self.root, 
                text="Aviso: Instale 'pillow' e 'pystray' no Python (pip install pillow pystray) para habilitar minimizar para a bandeja.",
                bg=self.bg_color, fg=self.text_muted, font=("Segoe UI", 8, "italic")
            )
            tray_notice.pack(side=tk.BOTTOM, fill=tk.X, pady=2)

    def populate_table(self):
        # Clear existing items
        for child in self.tree.get_children():
            self.tree.delete(child)
            
        # Add rules from config
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
            # Check if at least one rule is enabled
            if any(rule.get("enabled", False) for rule in self.config.get("rules", [])):
                self.root.after(500, self.start_monitoring)

    def toggle_monitor(self):
        if self.monitor.is_running():
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self):
        # Check if there are active rules first
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

    # Startup control via Windows Registry
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
        
        # Decide launch command
        # If run inside python executable, use sys.executable
        if getattr(sys, 'frozen', False):
            cmd = f'"{sys.executable}" --minimized'
        else:
            # If standard script, run with pythonw.exe (no console window)
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = "pythonw.exe"
            script_path = os.path.abspath(sys.argv[0])
            cmd = f'"{pythonw}" "{script_path}" --minimized'

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE)
            if enabled:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
                self.log_callback("[INFO] Inicialização com o Windows ativada com sucesso.")
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                    self.log_callback("[INFO] Inicialização com o Windows desativada.")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível ajustar registro de inicialização: {e}")
            self.start_with_windows_var.set(not enabled) # revert checkbox state

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

        # Style Dialog Inputs
        lbl_style = {"bg": self.bg_color, "fg": self.text_color, "font": ("Segoe UI", 9, "bold")}
        entry_style = {"bg": "#3d3d3d", "fg": "white", "insertbackground": "white", "relief": "flat", "bd": 1, "font": ("Segoe UI", 10)}

        # Main layout container
        form_frame = tk.Frame(dialog, bg=self.bg_color, padx=20, pady=20)
        form_frame.pack(fill=tk.BOTH, expand=True)

        # Title/Header
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

        # Horizontal group for Cleanup Days
        cleanup_group = tk.Frame(form_frame, bg=self.bg_color)
        cleanup_group.pack(fill=tk.X, pady=10)
        
        cleanup_frame = tk.Frame(cleanup_group, bg=self.bg_color)
        cleanup_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(cleanup_frame, text="Autolimpeza de Destino (dias, 0 para desativar):", **lbl_style).pack(anchor=tk.W, pady=(0, 2))
        entry_cleanup = tk.Entry(cleanup_frame, **entry_style, width=10)
        entry_cleanup.pack(anchor=tk.W)
        entry_cleanup.insert(0, str(rule_data.get("cleanup_days", 0)) if rule_data else "0")

        # Bottom buttons (Save / Cancel)
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
                # Update existing rule
                rule_data["name"] = name
                rule_data["source"] = src
                rule_data["destination"] = dest
                rule_data["interval"] = interval
                rule_data["cleanup_days"] = cleanup_days
                rule_data["enabled"] = enabled
                self.log_callback(f"[INFO] Regra '{name}' atualizada.")
            else:
                # Add new rule
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

        btn_save = tk.Button(
            actions_frame, text="Salvar Regra", bg=self.success_color, fg="black",
            relief="flat", bd=0, activebackground="#27ae60", activeforeground="black",
            font=("Segoe UI", 9, "bold"), command=save_rule, width=15, height=2
        )
        btn_save.pack(side=tk.RIGHT, padx=(10, 0))

        btn_cancel = tk.Button(
            actions_frame, text="Cancelar", bg=self.card_color, fg="white",
            relief="flat", bd=0, activebackground=self.border_color, activeforeground="white",
            font=("Segoe UI", 9, "bold"), command=dialog.destroy, width=12, height=2
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
            confirm = messagebox.askyesno("Confirmar Exclusão", f"Tem certeza que deseja excluir a regra '{rule.get('name')}'?")
            if confirm:
                self.config["rules"].remove(rule)
                self.save_config()
                self.populate_table()
                self.log_callback(f"[INFO] Regra '{rule.get('name')}' excluída.")

    # System Tray & Exit functions
    def on_close_window(self):
        if TRAY_AVAILABLE:
            self.minimize_to_tray()
        else:
            confirm = messagebox.askyesno("Sair", "Deseja parar o monitoramento e fechar o aplicativo?")
            if confirm:
                self.shutdown_app()

    def minimize_to_tray(self):
        self.root.withdraw() # Hide primary window
        
        # Setup system tray icon if not created yet
        if not self.tray_icon:
            # Try to load downloaded printer icon
            image = None
            if hasattr(self, 'icon_path') and os.path.exists(self.icon_path):
                try:
                    image = Image.open(self.icon_path)
                except Exception:
                    pass
            
            # Fallback to drawn icon if loading fails
            if not image:
                image = Image.new('RGB', (64, 64), color=(30, 30, 30))
                d = ImageDraw.Draw(image)
                # Draw a simulated scanner icon inside
                d.rectangle([16, 24, 48, 40], fill=(26, 115, 232), outline=(255, 255, 255))
                d.line([16, 32, 48, 32], fill=(255, 255, 255), width=2)
            
            menu = pystray.Menu(
                pystray.MenuItem("Abrir Painel", self.restore_from_tray, default=True),
                pystray.MenuItem("Iniciar Monitor", lambda: self.root.after(0, self.start_monitoring)),
                pystray.MenuItem("Parar Monitor", lambda: self.root.after(0, self.stop_monitoring)),
                pystray.MenuItem("Sair Completamente", self.shutdown_from_tray)
            )
            
            self.tray_icon = pystray.Icon("PrinterScanMover", image, "Monitor de Escaneamento", menu)
            
            # Start tray icon loop in background thread
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            
        self.log_callback("[INFO] Aplicativo minimizado para a bandeja do sistema (rodando em segundo plano).")

    def restore_from_tray(self, icon=None, item=None):
        if self.tray_icon:
            # In pystray, clicking item passes icon context
            self.root.deiconify() # Restore window
            self.root.focus_force() # Force focus

    def shutdown_from_tray(self, icon, item):
        self.tray_icon.stop()
        self.root.after(0, self.shutdown_app)

    def shutdown_app(self):
        if self.monitor.is_running():
            self.monitor.stop()
        self.root.destroy()
        sys.exit(0)


if __name__ == "__main__":
    root = tk.Tk()
    
    # Process "--minimized" startup parameter (used when starting with Windows)
    app = PrinterScanMoverApp(root)
    if "--minimized" in sys.argv:
        if TRAY_AVAILABLE:
            root.withdraw()
            root.after(1000, app.minimize_to_tray)
        else:
            # Fallback to iconic state if tray libs not present
            root.iconify()
            
    root.mainloop()
