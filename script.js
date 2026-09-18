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