"""Erros de integração com serviços externos (FTP, IMAP, SSH...).

Cada erro carrega um código estável (usado pelo frontend para distinguir
situações) e uma mensagem segura: nunca inclui senha, caminho de chave ou
detalhes internos.
"""


class ServiceError(Exception):
    status = 502
    code = "service_error"
    message = "Erro ao acessar o serviço."

    def __init__(self, message: str | None = None):
        if message:
            self.message = message
        super().__init__(self.message)


class InvalidInput(ServiceError):
    status = 400
    code = "invalid_input"
    message = "Dados inválidos."


class NotFound(ServiceError):
    status = 404
    code = "not_found"
    message = "Item não encontrado."


class Conflict(ServiceError):
    status = 409
    code = "conflict"
    message = "Conflito com o estado atual."


class PayloadTooLarge(ServiceError):
    status = 413
    code = "too_large"
    message = "Arquivo maior que o limite permitido."
