# Instalador do AUXILIUM

Esta pasta é a área independente do instalador. Ela não faz parte do aplicativo
instalado e não é copiada para `~/.local/share/auxilium`.

## Executar

Na raiz do projeto (ou do pacote portátil):

```bash
./instalador/INSTALAR
```

A janela nativa usa GTK3 + WebKit. Se WebKit não estiver disponível, a mesma
interface abre no navegador local, em `127.0.0.1`.

Para testar sem abrir janela:

```bash
python3 instalador/installer.py --no-window --port 8791
```

## Estrutura

| Caminho | Função |
| --- | --- |
| `INSTALAR` | Launcher shell que inicia `installer.py`. |
| `installer.py` | Servidor local, motor de cópia, validação, atalhos e janela GTK/WebKit. |
| `ui/index.html` | Assistente em quatro telas. |
| `ui/installer.css` | Identidade visual do instalador. |
| `ui/installer.js` | Estado, verificação de ambiente e progresso. |

O instalador lê o aplicativo na pasta pai. Ele nunca se copia para o destino e
não faz download de modelos; o primeiro uso do AUXILIUM continua sendo o ponto
de escolha do modelo.

## Distribuição

`scripts/build_portable.sh` gera um ZIP com o aplicativo e esta pasta
`instalador/` lado a lado. O arquivo `LEIA-ME.txt` na raiz do pacote explica o
fluxo para quem vai instalar.
