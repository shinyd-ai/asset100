/**
 * 30년 경력 전문가의 AI 자산 진단 로직
 */

document.addEventListener('DOMContentLoaded', () => {
    const btnStartAi = document.querySelector('#btn-start-ai');
    const aiContentArea = document.querySelector('#ai-content-area');
    const aiLoading = document.querySelector('#ai-loading');
    const aiResult = document.querySelector('#ai-result');

    if (btnStartAi) {
        btnStartAi.addEventListener('click', async () => {
            // UI 초기화
            aiContentArea.classList.remove('d-none');
            aiLoading.classList.remove('d-none');
            aiResult.innerHTML = '';
            btnStartAi.disabled = true;
            btnStartAi.innerText = '진단 중...';

            try {
                // 1단계 진단 요청 (향후 level 변수화 가능)
                const currentLevel = 1;
                const response = await fetch(`/api/ai-insight?level=${currentLevel}`);
                const data = await response.json();

                if (data.ok) {
                    // 약간의 시간을 주어 분석하는 느낌 연출
                    setTimeout(() => {
                        aiLoading.classList.add('d-none');
                        renderAiResult(data.analysis);
                        btnStartAi.innerText = '진단 완료 (1단계)';
                        btnStartAi.classList.replace('btn-primary', 'btn-outline-success');
                    }, 1500);
                } else {
                    throw new Error(data.error);
                }
            } catch (error) {
                console.error('AI Insight Error:', error);
                aiLoading.classList.add('d-none');
                aiResult.innerHTML = `<div class="alert alert-danger small">진단 중 오류가 발생했습니다: ${error.message}</div>`;
                btnStartAi.disabled = false;
                btnStartAi.innerText = '진단 재시도';
            }
        });
    }
});

/**
 * 마크다운 형식의 텍스트를 간단한 HTML로 렌더링
 * (전문 라이브러리를 쓰지 않고 정규식으로 간단히 파싱)
 */
function renderAiResult(text) {
    let html = text
        .replace(/^### (.*$)/gim, '<h3>$1</h3>')
        .replace(/^#### (.*$)/gim, '<h4>$1</h4>')
        .replace(/^> (.*$)/gim, '<blockquote>$1</blockquote>')
        .replace(/^\* (.*$)/gim, '<li>$1</li>')
        .replace(/^\- (.*$)/gim, '<li>$1</li>')
        .replace(/\*\*(.*)\*\*/gim, '<strong>$1</strong>')
        .replace(/\n\n/gim, '<br>');

    // 리스트 태그 감싸기
    if (html.includes('<li>')) {
        // 연속된 li를 ul로 감싸는 단순 처리
        html = html.replace(/(<li>.*<\/li>)/gms, '<ul>$1</ul>');
    }

    const aiResult = document.querySelector('#ai-result');
    aiResult.innerHTML = html;
}
