# Auditoria das dependências do backend

O workflow **Backend Security**, em `.github/workflows/backend-security.yml`, consulta os avisos de vulnerabilidades do PyPI usando `pip-audit`. Ele analisa separadamente os lockfiles Linux, Windows e da própria ferramenta de auditoria. Os pacotes transitivos também estão nesses arquivos e são consultados.

## Execução e resultado

- Push e pull request que alterem `backend/requirements*.in`, `backend/requirements*.txt`, `backend/security/` ou o workflow.
- Toda segunda-feira às 09:00 UTC, na branch padrão, depois que o workflow for integrado à `main`.
- Disparo manual por **Actions → Backend Security → Run workflow**, quando disponível na branch padrão.

No PR, confira os três checks **Auditoria (Linux)**, **Auditoria (Windows)** e **Auditoria (Ferramenta de auditoria)**. Um aviso conhecido, uma dependência que não possa ser consultada ou um erro de rede fazem o check falhar. Os três jobs são independentes: a falha de um não cancela os outros. Uma execução mais recente da mesma branch pode substituir a anterior.

O relatório mostra pacote, versão, identificador do aviso e versões com correção. Não há lista de vulnerabilidades ignoradas, atualização automática nem `continue-on-error`. A ação não faz deploy ou merge. Para impedir merges com falhas, um administrador precisa configurar a proteção da `main` exigindo esses checks; o workflow sozinho não aplica essa regra.

Cada job usa Python 3.12 em Linux e instala somente a ferramenta, em ambiente virtual separado, exigindo os hashes de `backend/security/requirements.txt`. A consulta usa `--disable-pip --require-hashes --strict`: lê as versões fixadas, sem instalar, compilar ou resolver os pacotes da aplicação. Os lockfiles atuais contêm versões exatas sem marcadores de plataforma; assim, o arquivo Windows também inclui a consulta de `tzdata` no runner Linux. Isso não representa execução nativa da aplicação no Windows.

As ações estão fixadas por revisão, o token tem somente leitura e o checkout não retém credenciais Git. Não são necessários banco, SMTP, gateway ou segredos de produção. Somente nomes e versões de pacotes públicos são consultados no PyPI por HTTPS. O cache da consulta é temporário de cada job; os relatórios aparecem nos logs do GitHub, sem upload de banco ou de arquivos de configuração.

## Reproduzir no Windows

Na raiz do projeto, com Python 3.12 de 64 bits, use um ambiente separado da `.venv` da aplicação:

```powershell
py -3.12 -m venv backend\.local\audit-venv
backend\.local\audit-venv\Scripts\python.exe -m pip install --require-hashes -r backend\security\requirements.txt
backend\.local\audit-venv\Scripts\python.exe -m pip_audit --require-hashes --disable-pip --strict --progress-spinner off --timeout 30 --cache-dir backend\.local\audit-cache --requirement backend\requirements.txt
backend\.local\audit-venv\Scripts\python.exe -m pip_audit --require-hashes --disable-pip --strict --progress-spinner off --timeout 30 --cache-dir backend\.local\audit-cache --requirement backend\requirements-windows.txt
backend\.local\audit-venv\Scripts\python.exe -m pip_audit --require-hashes --disable-pip --strict --progress-spinner off --timeout 30 --cache-dir backend\.local\audit-cache --requirement backend\security\requirements.txt
```

Execute as três consultas e confira cada resultado. Código de saída zero significa que nenhum aviso conhecido foi encontrado naquela consulta; resultado diferente de zero precisa de análise. Não é necessário iniciar o site ou o PostgreSQL para auditar.

No Linux, crie o ambiente com `python3.12 -m venv backend/.local/audit-venv` e substitua o executável dos comandos por `backend/.local/audit-venv/bin/python`, usando `/` nos caminhos.

## Como tratar uma falha

1. Abra o aviso pelo identificador mostrado, confirme o pacote e leia a correção indicada pelo mantenedor. Uma falha de conexão precisa de uma nova consulta bem-sucedida; não equivale a ausência de vulnerabilidades.
2. Para a aplicação, atualize a versão em `backend/requirements.in` e regenere **ambos** os lockfiles com hashes, preservando as versões dos demais pacotes quando possível. Para a ferramenta, use `backend/security/requirements.in` e regenere seu lockfile separado.
3. Instale os lockfiles com `--require-hashes`, repita as três auditorias e execute o **Backend CI** com PostgreSQL. Confira compatibilidade e notas de mudança antes de integrar a atualização.
4. Revise e integre o PR. A auditoria não atualiza o ambiente do CTIC ou uma instalação local: cada ambiente precisa receber e instalar os arquivos revisados pelo processo de implantação correspondente.

Não use `--fix` durante o CI: ele alteraria os arquivos sem a revisão e os testes da atualização. Se surgir um aviso sem correção disponível, registre a análise e a mitigação antes de considerar qualquer exceção explícita e revisada.

## Atualização incluída nesta entrega

A consulta inicial encontrou avisos em `cryptography==46.0.5`. A versão foi atualizada para **50.0.2** em Linux e Windows, mantendo as versões dos demais pacotes da aplicação. Os avisos incluíam problemas de validação X.509, buffers, decriptação PKCS#7 e OpenSSL distribuído nos wheels. Referências: [notas oficiais da versão e anteriores](https://github.com/pyca/cryptography/blob/50.0.2/CHANGELOG.rst) e [avisos de segurança do mantenedor](https://github.com/pyca/cryptography/security/advisories).

O backend utiliza Fernet para os segredos MFA. Os avisos não demonstram exploração desse uso específico, mas justificam atualizar a biblioteca. A atualização não exige migração do banco ou troca da chave MFA. Preserve a chave existente: substituí-la impediria a leitura dos segredos já cadastrados. Python de 32 bits no Windows deixou de ser suportado por essa biblioteca; o projeto já orienta Python 3.12 de 64 bits.

Uma auditoria sem achados significa ausência de avisos conhecidos na base consultada naquele momento. Ela complementa os testes e a revisão do código; não verifica por si só o sistema operacional do CTIC, os serviços externos, o frontend ou vulnerabilidades ainda desconhecidas.
