from urllib.parse import urlsplit

import requests


class ApiCaptura:
    def __init__(self, base):
        partes = urlsplit(base)
        if partes.scheme not in ("http", "https") or not partes.hostname or partes.username or partes.password or partes.query or partes.fragment:
            raise ValueError("Configure WEB_DATA_VIZ_URL com uma URL de API válida, sem credenciais.")
        self.base = base.rstrip("/")
        self.sessao = requests.Session()
        self.csrf = None
        self._credenciais = None

    def _enviar(self, metodo, rota, dados=None):
        headers = {"X-CSRF-Token": self.csrf} if self.csrf and metodo == "POST" else {}
        try:
            return self.sessao.request(metodo, self.base + rota, json=dados,
                                      headers=headers, timeout=(5, 20), allow_redirects=False)
        except (requests.exceptions.RequestException, OSError):
            raise ValueError("Falha de comunicação com a API; confira conexão e configuração.") from None

    @staticmethod
    def _dados(resposta):
        try:
            return resposta.json()
        except (ValueError, TypeError):
            raise ValueError("A API retornou uma resposta JSON inválida.") from None

    def autenticar(self, email, senha):
        if not isinstance(email, str) or not email or not isinstance(senha, str) or not senha:
            raise ValueError("Informe as credenciais de acesso ao StreamGuard.")
        self.csrf = None
        self.sessao.cookies.clear()
        resposta = self._enviar("POST", "/usuarios/autenticar", {"emailServer": email, "senhaServer": senha})
        if resposta.status_code != 200:
            raise ValueError(f"Autenticação não confirmada pela API (HTTP {resposta.status_code}).")
        dados = self._dados(resposta)
        token = resposta.headers.get("X-CSRF-Token", "")
        if not isinstance(dados, dict) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("A API não confirmou uma sessão com proteção CSRF.")
        self.csrf = token
        self._credenciais = (email, senha)
        return dados

    def pedir(self, metodo, rota, dados=None):
        resposta = self._enviar(metodo, rota, dados)
        if resposta.status_code == 401 and self._credenciais:
            self.autenticar(*self._credenciais)
            resposta = self._enviar(metodo, rota, dados)
        if resposta.status_code not in (200, 201):
            if resposta.status_code == 409:
                raise ValueError("Identidade ou cadastro conflitante; revise os registros existentes antes de continuar.")
            if resposta.status_code == 403:
                raise ValueError("Operação não autorizada para a empresa ou sessão atual.")
            raise ValueError(f"Operação não confirmada pela API (HTTP {resposta.status_code}).")
        return self._dados(resposta)

    def fechar(self):
        self._credenciais = None
        self.csrf = None
        self.sessao.close()
