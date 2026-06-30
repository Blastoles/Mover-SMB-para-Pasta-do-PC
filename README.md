# Monitor de Escaneamento Multimpressoras (Printer Scan File Mover)

Este aplicativo em Python monitora pastas de rede (via protocolo SMB/caminhos UNC) ou locais, criadas por impressoras e multifuncionais ao digitalizar documentos, e move automaticamente os arquivos gerados para pastas locais customizadas em seu computador. 

Ele conta com uma interface gráfica moderna (Modo Escuro), suporte a execução em segundo plano através da bandeja do sistema (System Tray) e inicialização automática com o Windows.

---

## 📥 Download Direto (.exe)

Se você não possui o Python instalado ou apenas deseja rodar o programa diretamente:
1. Acesse a aba **Releases** na lateral direita deste repositório no GitHub.
2. Baixe o executável `PrinterScanMover.exe` da versão mais recente.
3. Execute o arquivo diretamente no seu computador.

---

## 🚀 Funcionalidades

- **Monitoramento de Múltiplos Locais**: Configure várias regras de monitoramento com caminhos de origem (ex: `\\IP_DA_IMPRESSORA\scans`) e destino específicos.
- **Detecção de Estabilidade do Arquivo**: O utilitário só move o arquivo após certificar-se de que ele não está mais sendo escrito pela impressora, evitando arquivos corrompidos.
- **Autolimpeza Automatizada**: Opção para apagar automaticamente arquivos na pasta de destino que sejam mais antigos que um número específico de dias.
- **Minimizar para a Bandeja (System Tray)**: Permite que o programa continue rodando em segundo plano de forma oculta.
- **Iniciar com o Windows**: Opção integrada para inicializar o aplicativo silenciosamente no boot do Windows (gerenciado via Registro do Windows).
- **Interface Moderna**: Desenvolvida com Tkinter em tema escuro com logs de atividade detalhados em tempo real.

---

## 📁 Estrutura do Projeto

- **[main.py](main.py)**: Responsável pela interface gráfica (GUI), gerenciamento do ícone de bandeja (System Tray) e registro de inicialização automática no Windows.
- **[monitor.py](monitor.py)**: Engine que executa a lógica de varredura das pastas em threads de segundo plano, validação de escrita e transferência dos arquivos.
- **[config.json](config.json)**: Armazena as configurações globais do aplicativo e as regras cadastradas (caminhos, intervalos e limpeza).
- **[requirements.txt](requirements.txt)**: Dependências necessárias para executar e habilitar os recursos visuais de segundo plano.

---

## 🛠️ Pré-requisitos

- Python 3.8 ou superior.
- Permissão de leitura na pasta de origem (se estiver em rede, certifique-se de que a máquina possui acesso e credenciais salvas para o caminho UNC especificado).

---

## 💻 Instalação

1. Clone ou baixe este repositório no seu computador.
2. Abra o terminal no diretório do projeto e instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

---

## 🕹️ Como Usar

### Executando em Modo de Desenvolvimento
Para iniciar a interface gráfica do programa:
```bash
python main.py
```

### Configurando uma Regra
1. Clique em **"Adicionar Regra"**.
2. Defina um nome identificador para a impressora.
3. Preencha a **Pasta de Origem** (ex: `\\IP_DA_IMPRESSORA\scans` ou uma pasta local).
4. Preencha a **Pasta de Destino** (onde os arquivos devem ser salvos no seu PC).
5. Defina o **Intervalo de Checagem** em segundos (o padrão é 5s).
6. Se desejar que os arquivos antigos na pasta de destino sejam excluídos automaticamente após algum tempo, insira a quantidade de dias no campo **Autolimpeza** (0 desativa o recurso).
7. Clique em **"Salvar Regra"** e depois em **"Iniciar Monitor"**.

---

## 📦 Como Gerar o Executável (.exe)

O projeto já contém um arquivo de especificação do **PyInstaller** (`PrinterScanMover.spec`) configurado para compilar a aplicação de forma limpa, ocultando a janela do terminal no Windows.

1. Instale o PyInstaller:
   ```bash
   pip install pyinstaller
   ```
2. Gere o executável executando o comando:
   ```bash
   pyinstaller PrinterScanMover.spec
   ```
3. O executável standalone será gerado dentro da pasta `dist/PrinterScanMover.exe`.
