from urllib.parse import urlsplit

import requests


class ApiCaptura:
    def __init__(self, base):
        partes = urlsplit(base)
        if partes.scheme not in ("http", "https") or not partes.hostname or partes.username or partes.password or partes.query or partes.fragment:
            raise ValueError("Configure WEB_DATA_VIZ_URL com uma URL de API válida, sem credenciais.")
        self.base = base.rstrip("/")
        self.sessao = requests.Session()
        self.empresa_id = None

    def _enviar(self, metodo, rota, dados=None):
        try:
            return self.sessao.request(metodo, self.base + rota, json=dados,
                                      timeout=(5, 20), allow_redirects=False)
        except (requests.exceptions.RequestException, OSError):
            raise ValueError("Falha de comunicação com a API; confira conexão e configuração.") from None

    @staticmethod
    def _dados(resposta):
        try:
            return resposta.json()
        except (ValueError, TypeError):
            raise ValueError("A API retornou uma resposta JSON inválida.") from None

    def autenticar(self, email, senha):
        self.empresa_id = None
        if not isinstance(email, str) or not email or not isinstance(senha, str) or not senha:
            raise ValueError("Informe as credenciais de acesso ao StreamGuard.")
        self.sessao.cookies.clear()
        resposta = self._enviar("POST", "/usuarios/autenticar", {"emailServer": email, "senhaServer": senha})
        if resposta.status_code != 200:
            raise ValueError(f"Autenticação não confirmada pela API (HTTP {resposta.status_code}).")
        dados = self._dados(resposta)
        if not isinstance(dados, dict):
            raise ValueError("A API não retornou um objeto de autenticação válido.")
        empresa = dados.get("empresa_id")
        if type(empresa) is not int or empresa <= 0:
            raise ValueError("O usuário autenticado não possui uma empresa disponível para captura.")
        # A API atual retorna a empresa no login, sem cookie ou token CSRF.
        self.empresa_id = empresa
        return dados

    def pedir(self, metodo, rota, dados=None):
        if self.empresa_id is None:
            raise ValueError("Autentique na API antes de consultar ou cadastrar recursos.")
        resposta = self._enviar(metodo, rota, dados)
        if resposta.status_code not in (200, 201):
            if resposta.status_code == 409:
                raise ValueError("Identidade ou cadastro conflitante; revise os registros existentes antes de continuar.")
            if resposta.status_code in (401, 403):
                self.empresa_id = None
                raise ValueError("Operação não autorizada; confira o login e a empresa antes de tentar novamente.")
            raise ValueError(f"Operação não confirmada pela API (HTTP {resposta.status_code}).")
        return self._dados(resposta)

    def fechar(self):
        self.empresa_id = None
        self.sessao.close()
