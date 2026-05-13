import re
from typing import Dict, List, Optional

SAFE_INSPECTION_RULES = [
    (r"^(get-date|pwd|get-location|whoami|hostname)\b", ["inspection"]),
    (r"^(dir|ls|get-childitem)\b", ["inspection", "filesystem"]),
    (r"^(echo|write-output)\b", ["inspection"]),
    (r"^(type|get-content)\b", ["inspection", "filesystem"]),
    (r"^(get-process|get-service|get-command|get-psdrive|get-smbshare)\b", ["inspection", "system"]),
    (r"^(get-printer|get-printerport|get-printerdriver)\b", ["inspection", "printer"]),
    (r"^(python|py|node|npm|pip)\s+--version\b", ["inspection"]),
    (r"^net\s+use\b", ["inspection", "network"]),
]

RISK_RULES = [
    (r"\b(format(?!-table\b)(?!-list\b)|shutdown|stop-computer|restart-computer|diskpart)\b", "high", ["destructive", "system"]),
    (r"\b(remove-item|del|erase|rm|rmdir|rd)\b.*(\-recurse|\-force|/s|[A-Za-z]:\\)", "high", ["destructive", "filesystem"]),
    (r"\b(reg\s+(add|delete|deletevalue)|sc\s+delete|bcdedit|takeown|icacls|cipher\s+/w)\b", "high", ["system", "registry"]),
    (r"\b(net\s+user|net\s+localgroup)\b", "high", ["accounts", "system"]),
    (r"\b(choco|winget)\s+(install|upgrade|uninstall)\b", "medium", ["installer", "system"]),
    (r"\b(msiexec\s+/i|python\s+-m\s+pip\s+(install|uninstall|install\s+--upgrade)|py\s+-m\s+pip\s+(install|uninstall|install\s+--upgrade))\b", "medium", ["installer"]),
    (r"\b(curl|wget|invoke-webrequest|start-bitstransfer|certutil\b.*-urlcache)\b", "medium", ["network"]),
    (r"\b(start-process|new-item|set-content|add-content|copy-item|move-item|rename-item)\b", "medium", ["mutation"]),
    (r"\b(powershell|cmd)(?:\.exe)?\b|\|", "low", ["shell"]),
]


def normalize_command(command: str) -> str:
    normalized = (command or "").strip()
    normalized = re.sub(r"^\s*(powershell|pwsh)(?:\.exe)?\s+(-command|-c)\s+", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"^\s*cmd(?:\.exe)?\s+/c\s+", "", normalized, flags=re.IGNORECASE)
    return normalized.strip().strip("\"' ")


def classify(command: str) -> Dict:
    normalized = normalize_command(command)
    tags: List[str] = []
    lowered = normalized.lower()

    for pattern, rule_tags in SAFE_INSPECTION_RULES:
        if re.search(pattern, lowered, re.IGNORECASE):
            return {"level": "low", "tags": list(set(rule_tags + ["shell"])), "reason": "safe inspection command"}

    level = "low"
    for pattern, p_level, p_tags in RISK_RULES:
        if re.search(pattern, normalized, re.IGNORECASE):
            tags.extend(p_tags)
            if p_level == "high":
                level = "high"
                break
            if p_level == "medium" and level != "high":
                level = "medium"

    return {
        "level": level,
        "tags": list(set(tags or ["shell"])),
        "reason": "matched rules" if tags else "no specific match",
    }


def explain_command(command: str, risk: Optional[Dict] = None) -> str:
    normalized = normalize_command(command)
    lowered = normalized.lower()
    risk = risk or classify(command)

    explanations = [
        (r"\bwmic\s+printer\s+get\b", "Vai listar as impressoras conhecidas pelo Windows, incluindo nome, driver, porta e se sao de rede."),
        (r"\bget-printer\b", "Vai listar as impressoras configuradas neste computador."),
        (r"\bipconfig\b.*\|\s*findstr\b.*ipv4", "Vai mostrar apenas os enderecos IPv4 ativos das interfaces de rede."),
        (r"\bipconfig\b", "Vai mostrar a configuracao atual de rede deste computador."),
        (r"\bnetsh\s+wlan\s+show\s+interfaces\b", "Vai mostrar o estado da conexao Wi-Fi e os detalhes da interface sem fio."),
        (r"\bdriverquery\b", "Vai listar os drivers atualmente carregados ou instalados no Windows."),
        (r"\bpnputil\b", "Vai consultar ou alterar pacotes de driver e dispositivos Plug and Play no Windows."),
        (r"\bget-psdrive\b", "Vai listar os drives disponiveis no Windows, incluindo unidades mapeadas e compartilhamentos acessiveis."),
        (r"\bnet\s+use\b", "Vai listar os compartilhamentos de rede e unidades mapeadas conhecidas nesta sessao."),
        (r"\bsc\s+(query|qc)\b", "Vai consultar o estado ou a configuracao de um servico do Windows."),
        (r"\btasklist\b", "Vai listar os processos em execucao."),
        (r"\bwhoami\b", "Vai mostrar qual usuario esta executando esta sessao."),
        (r"\bhostname\b", "Vai mostrar o nome deste computador na rede."),
        (r"\bget-date\b", "Vai mostrar a data e a hora atuais."),
        (r"\bwinget\s+install\b|\bchoco\s+install\b|\bmsiexec\s+/i\b|\bpip\s+install\b", "Vai instalar software ou dependencias neste computador."),
        (r"\bremove-item\b|\bdel\b|\berase\b|\brm\b|\brmdir\b|\brd\b", "Vai remover arquivos ou diretorios do computador."),
        (r"\bshutdown\b|\brestart-computer\b|\bstop-computer\b", "Vai desligar ou reiniciar o computador."),
        (r"\breg\s+(add|delete)\b|\bbcdedit\b", "Vai alterar configuracoes sensiveis do Windows."),
    ]

    for pattern, explanation in explanations:
        if re.search(pattern, lowered, re.IGNORECASE):
            return explanation

    if risk.get("level") == "high":
        return "Vai executar um comando com potencial de alterar partes sensiveis do sistema."
    if risk.get("level") == "medium":
        return "Vai executar um comando que altera configuracoes, arquivos ou software no computador."
    return "Vai executar um comando de consulta ou diagnostico no Windows."
