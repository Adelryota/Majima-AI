document.addEventListener('DOMContentLoaded', () => {
    // Total duration of the intro sequence before exit
    // Title (1.5s) + Subtitle Delay (1.2s) + Subtitle Animation (1s) + Pause (1.5s)
    const introDuration = 4500;

    setTimeout(() => {
        // Trigger exit animation
        document.body.classList.add('fade-out');

        // Wait for exit animation to finish (0.8s) then redirect
        setTimeout(() => {
            window.location.href = '/login';
        }, 800);

    }, introDuration);
});
