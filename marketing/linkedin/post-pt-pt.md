# Publicação LinkedIn (PT-PT, opcional)

---

A maior parte das demos de IA para apoio ao cliente mostra o que o modelo consegue escrever. Eu quis perceber o que é preciso para deixar esse texto chegar perto de um cliente.

Por isso criei o support-copilot, um projeto independente de conceito, com dados sintéticos. A IA prepara o ticket: triagem, uma resposta no idioma do cliente e um aviso se o rascunho prometer algo que não deve. Uma pessoa aprova ou rejeita cada resposta. Nada chega ao cliente antes disso.

Quatro decisões que defendo:

1. O modelo nunca vê dados privados em bruto. Emails, cartões, IBAN, telefones e palavras-passe são mascarados primeiro por regras simples (com verificações Luhn e mod-97, para que um número de encomenda não seja apagado).

2. As promessas são verificadas por regras, não pedindo ao modelo que se porte bem. Se um rascunho escrito pelo modelo prometer um reembolso, é substituído pela resposta-padrão. Se for o revisor a escrevê-lo, a página avisa e pede um segundo clique.

3. A falha é visível. Se o modelo recusar, for cortado a meio ou estiver em baixo, o ticket é na mesma triado por regras e o ecrã di-lo.

4. As decisões humanas tornam-se dados. Por categoria, a aplicação mostra com que frequência a IA acertou, com um limite de confiança: três revisões perfeitas não são evidência. "Candidata a automação" é apenas informativo. Nada é automatizado.

Numa falha de serviço, os tickets quase idênticos ficam agrupados num possível incidente e respondidos com um clique, continuando a ser revisões individuais.

Testes: 332 testes offline, com cada história de utilizador como teste de aceitação executável. Também estraguei o código de propósito, de dezenas de maneiras, para confirmar que um teste falhava de cada vez.

O que não é: os adaptadores de Zammad e Zendesk foram testados contra simulações, o caminho do Claude contra um SDK simulado, e as proteções têm lacunas documentadas (nomes e moradas não são mascarados; uma promessa com redação invulgar passa). Uma pessoa continua a ler cada resposta. Prefiro dizê-lo com clareza.

A ideia a que volto sempre: um sistema de IA útil precisa de mais do que bons resultados. Precisa de limites claros, verificações determinísticas onde fazem sentido, juízo humano e comportamento que se possa testar.

Código, capturas de ecrã e página de limitações: https://github.com/monicaslgc/support-copilot

#GestãoDeProduto #IAAplicada #HumanInTheLoop #IAResponsável #ApoioAoCliente #Automação
