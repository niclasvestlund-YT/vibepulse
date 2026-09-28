chrome.storage.local.get('status').then(({status}) => {
  document.getElementById('status').textContent = status || 'Waiting for a balance…';
});
