# Publicação LinkedIn (PT-PT)

Estrutura do kit: gancho, contexto, trabalho, aprendizagem, limitações, CTA.

---

A IA propõe, regras determinísticas decidem, as pessoas aprovam. Quis ver como isto cabe dentro do helpdesk que uma equipa de apoio ao cliente já usa, em vez de ficar ao lado.

A maior parte das demos de IA para apoio ao cliente é mais uma aplicação. Uma equipa já tem um helpdesk e ninguém quer abrir mais uma ferramenta.

Por isso criei o support-copilot como um complemento, um projeto independente de conceito, com dados sintéticos. Ouve o helpdesk (Zendesk, Zammad ou o vosso), faz a triagem de cada ticket, prepara uma resposta no idioma do cliente a partir dos vossos artigos de ajuda e escreve o resultado de volta como nota interna e etiquetas. O agente decide sem sair do ticket: junta a etiqueta ai_approve e a resposta preparada segue para o cliente, junta ai_reject e responde ele próprio. Nada chega ao cliente antes disso.

Quatro decisões que defendo:

1. As etiquetas são a interface. Os vossos acionadores e vistas podem encaminhar com base nelas, os nomes são vossos, e o complemento nunca precisa de conhecer os vossos grupos ou campos personalizados.

2. Uma decisão é assinada. Aprovar por etiqueta envia uma resposta a um cliente, por isso, num helpdesk ligado, só é aceite quando a assinatura do webhook pode ser verificada, e podem limitá-la às pessoas que indicarem.

3. Regras simples rodeiam o modelo. Os dados privados são mascarados antes dele, com verificações Luhn e mod-97 para que um número de encomenda fique intacto. As promessas são apanhadas depois: um rascunho que prometa um reembolso é substituído pela resposta padrão.

4. As decisões humanas tornam-se dados. Por categoria, mostra quantas vezes a IA acertou, com um limite de confiança: três revisões perfeitas não são evidência. O "candidato a automação" é só informativo. Nada é automatizado.

O que levo daqui: um sistema de IA útil precisa de limites claros, verificações determinísticas onde fazem sentido, juízo humano e comportamento que se possa testar. Por isso tem 385 testes offline, com cada uma das doze histórias de utilizador como teste de aceitação executável. É uma biblioteca Python com um router FastAPI opcional e uma página de revisão opcional, para que uma equipa use só a parte de que precisa.

O que não é: os adaptadores de Zammad e Zendesk foram testados contra simulações, e o caminho do Claude contra um SDK simulado. Nada correu ainda num helpdesk real nem na API real, incluindo os payloads que trazem as etiquetas. As salvaguardas têm lacunas documentadas (nomes e moradas não são mascarados; uma promessa com redação estranha passa). Uma pessoa continua a ler todas as respostas. Prefiro dizê-lo com clareza.

Código, capturas de ecrã e a página de limitações: https://github.com/monicaslgc/support-copilot

#AI #ProductManagement #Automation #OpenSource #BusinessAnalysis
