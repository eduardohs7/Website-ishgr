document.addEventListener("DOMContentLoaded", () => {
    const hamburger = document.querySelector(".hamburger");
    const navLinks = document.querySelector(".nav-links");

    // Alterna a classe 'active' para mostrar/esconder o menu mobile
    hamburger.addEventListener("click", () => {
        navLinks.classList.toggle("active");
        hamburger.classList.toggle("active");
    });
});

document.addEventListener('DOMContentLoaded', () => {
    // 1. Seleciona os elementos da tela
    const inputBusca = document.querySelector('.filtro-busca input');
    const selectTopico = document.querySelector('.filtro-topico select');
    const cardsTrabalho = document.querySelectorAll('.trabalho-card');
    const contadorResultados = document.querySelector('.resultados-count');

    // Função que aplica os filtros
    function filtrarTrabalhos() {
        // Pega o que o usuário digitou e converte para minúsculas
        const termoBusca = inputBusca.value.toLowerCase();
        // Pega a opção escolhida no select
        const topicoEscolhido = selectTopico.value;
        
        let trabalhosVisiveis = 0;

        // 2. Passa por cada card de trabalho na página
        cardsTrabalho.forEach(card => {
            // Extrai os textos do card
            const titulo = card.querySelector('.trabalho-titulo').textContent.toLowerCase();
            const autores = card.querySelector('.trabalho-autores').textContent.toLowerCase();
            const topicoCard = card.querySelector('.topico-badge').textContent;

            // 3. Verifica se bate com a busca e o filtro
            const bateComBusca = titulo.includes(termoBusca) || autores.includes(termoBusca);
            const bateComTopico = (topicoEscolhido === 'Todos os tópicos') || (topicoCard === topicoEscolhido);

            // 4. Mostra ou esconde o card
            if (bateComBusca && bateComTopico) {
                card.style.display = 'flex'; // Mostra o card
                trabalhosVisiveis++;
            } else {
                card.style.display = 'none'; // Esconde o card
            }
        });

        // 5. Atualiza o número no contador
        if (contadorResultados) {
            const numeroFormatado = trabalhosVisiveis < 10 ? `0${trabalhosVisiveis}` : trabalhosVisiveis;
            const textoPlural = trabalhosVisiveis === 1 ? 'trabalho encontrado' : 'trabalhos encontrados';
            contadorResultados.innerHTML = `<strong>${numeroFormatado}</strong> ${textoPlural}`;
        }
    }

    // 6. Fica "ouvindo" as ações do usuário
    if (inputBusca && selectTopico) {
        // Dispara o filtro toda vez que o usuário digitar uma letra
        inputBusca.addEventListener('input', filtrarTrabalhos);
        // Dispara o filtro quando mudar a opção do select
        selectTopico.addEventListener('change', filtrarTrabalhos);
    }
});


// Ação para expandir os cards da Programação
const itemsExpansiveis = document.querySelectorAll('.timeline-item.expandable');

itemsExpansiveis.forEach(item => {
    item.addEventListener('click', () => {
        // Alterna a classe 'open' para abrir ou fechar
        item.classList.toggle('open');
    });
});

// Filtro da página de Atrações
const tabBtns = document.querySelectorAll('.tab-btn');
const atracoesCards = document.querySelectorAll('.atracao-card');

if (tabBtns.length > 0 && atracoesCards.length > 0) {
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            // Remove classe ativa de todos os botões e adiciona no clicado
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // Pega o filtro selecionado
            const filtro = btn.getAttribute('data-filter');

            // Mostra ou esconde os cards baseado no filtro
            atracoesCards.forEach(card => {
                const categoriaCard = card.getAttribute('data-category');
                
                if (filtro === 'todos' || categoriaCard === filtro) {
                    card.style.display = 'flex'; // ou block, dependendo do CSS base
                } else {
                    card.style.display = 'none';
                }
            });
        });
    });
}

/* ==========================================================================
   MODAL DA PÁGINA DE ATRAÇÕES (CORRIGIDO)
   ========================================================================== */
document.addEventListener('DOMContentLoaded', () => {
    const botoesDetalhes = document.querySelectorAll('.btn-detalhes');
    const modalConvidado = document.getElementById('modal-convidado');
    
    if (botoesDetalhes.length > 0 && modalConvidado) {
        const btnFechar = modalConvidado.querySelector('.close-modal');

        botoesDetalhes.forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault(); // Impede a tela de piscar ou travar
                
                const card = btn.closest('.atracao-card');
                
                // Extrai os dados
                const nome = card.querySelector('.card-nome').innerText;
                const inst = card.querySelector('.card-inst').innerText;
                const tema = card.querySelector('.card-tema').innerText;
                const bio = card.querySelector('.hidden-bio').innerHTML;
                const tipo = card.querySelector('.card-tipo').innerText;

                // Preenche o modal
                document.getElementById('modal-nome').innerText = nome;
                document.getElementById('modal-inst').innerText = inst;
                document.getElementById('modal-tema').innerText = tema;
                document.getElementById('modal-bio').innerHTML = bio;
                document.getElementById('modal-badge').innerText = tipo;

                // Mostra o modal forçando o layout flex
                modalConvidado.style.display = 'flex';
            });
        });

        if (btnFechar) {
            btnFechar.addEventListener('click', () => {
                modalConvidado.style.display = 'none';
            });
        }

        modalConvidado.addEventListener('click', (e) => {
            if (e.target === modalConvidado) {
                modalConvidado.style.display = 'none';
            }
        });
    }
});
// ==========================================================================
// DESTAQUE AUTOMÁTICO DO LINK ATIVO NA NAVBAR
// ==========================================================================
document.addEventListener('DOMContentLoaded', () => {
    // 1. Obtém o nome do ficheiro atual no URL (ex: "contato.html")
    const currentPath = window.location.pathname;
    let pageName = currentPath.split('/').pop(); 
    
    // Se o URL estiver vazio na raiz (ex: www.seusite.com/), assume que é a home
    if (pageName === '') {
        pageName = 'index.html';
    }

    // 2. Seleciona todos os links dentro da navbar
    const navLinks = document.querySelectorAll('.nav-links a');

    // 3. Percorre cada link para verificar se corresponde à página atual
    navLinks.forEach(link => {
        // Primeiro, removemos a classe active de todos por precaução
        link.classList.remove('active');

        const linkHref = link.getAttribute('href');

        // Se o href do link for exatamente igual ao nome da página atual, adicionamos a classe
        if (linkHref === pageName) {
            link.classList.add('active');
        }
    });
});

/* ==========================================================================
   MODAL DA PÁGINA DE PROGRAMAÇÃO
   ========================================================================== */
document.addEventListener('DOMContentLoaded', () => {
    const botoesAbrirProg = document.querySelectorAll('.btn-abrir-modal-prog');
    const modalProg = document.getElementById('modal-prog');
    
    if (botoesAbrirProg.length > 0 && modalProg) {
        const btnFecharProg = document.querySelector('.close-modal-prog');

        botoesAbrirProg.forEach(btn => {
            btn.addEventListener('click', (e) => {
                // Encontra o card pai daquele botão clicado
                const card = btn.closest('.atividade-card');
                
                // Extrai as informações
                const tipoHTML = card.querySelector('.prog-type').cloneNode(true);
                const horario = card.querySelector('.prog-time').innerText;
                const titulo = card.querySelector('.prog-title').innerText;
                const palestranteHTML = card.querySelector('.data-palestrante').innerHTML;
                const localHTML = card.querySelector('.data-local').innerHTML;

                // Injeta no modal
                document.getElementById('modal-prog-tipo').innerHTML = '';
                document.getElementById('modal-prog-tipo').appendChild(tipoHTML);
                document.getElementById('modal-prog-horario').innerText = horario;
                document.getElementById('modal-prog-titulo').innerText = titulo;
                document.getElementById('modal-prog-palestrante').innerHTML = palestranteHTML;
                document.getElementById('modal-prog-local').innerHTML = localHTML;

                // Abre o modal (o flex aplica o display flex que estava oculto)
                modalProg.style.display = 'flex';
            });
        });

        // Fechar pelo botão X
        btnFecharProg.addEventListener('click', () => {
            modalProg.style.display = 'none';
        });

        // Fechar clicando no fundo escuro fora do modal
        modalProg.addEventListener('click', (e) => {
            if (e.target === modalProg) {
                modalProg.style.display = 'none';
            }
        });
    }
});