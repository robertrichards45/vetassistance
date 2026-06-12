// Veteran Benefits Assistance — main.js
(function(){
  function ready(fn){
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function(){
    // Director dropdown toggle (existing behavior if markup uses .nav-dropdown)
    document.querySelectorAll('.nav-dropdown .nav-dropbtn').forEach(function(btn){
      btn.addEventListener('click', function(e){
        e.preventDefault();
        var wrap = btn.closest('.nav-dropdown');
        if (!wrap) return;
        wrap.classList.toggle('open');
      });
    });
    document.addEventListener('click', function(e){
      document.querySelectorAll('.nav-dropdown.open').forEach(function(wrap){
        if (!wrap.contains(e.target)) wrap.classList.remove('open');
      });
    });

    // AI scan: show/hide low confidence blocks (staff only)
    var toggleBtn = document.getElementById('toggleLowConfBtn');
    if (toggleBtn){
      toggleBtn.addEventListener('click', function(){
        var open = toggleBtn.getAttribute('data-open') === '1';
        var blocks = document.querySelectorAll('.ai-claim.low-conf');
        blocks.forEach(function(el){
          el.style.display = open ? 'none' : 'block';
        });
        toggleBtn.setAttribute('data-open', open ? '0' : '1');
        toggleBtn.textContent = open ? 'Show low-confidence (' + (toggleBtn.dataset.threshold || '') + '%+ hidden)' : 'Hide low-confidence';
      });
    }
  });
})();