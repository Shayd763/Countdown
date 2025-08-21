const CACHE_NAME = 'oct-1-countdown-v1';
const urlsToCache = [
  '/',
  '/index.html',
  '/manifest.json'
];

// Install event - cache resources
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
      .catch((error) => {
        console.error('Failed to cache resources:', error);
      })
  );
});

// Fetch event - serve cached content when offline
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request)
      .then((response) => {
        // Return cached version or fetch from network
        if (response) {
          return response;
        }
        
        // Clone the request because it's a stream
        const fetchRequest = event.request.clone();
        
        return fetch(fetchRequest).then((response) => {
          // Check if we received a valid response
          if (!response || response.status !== 200 || response.type !== 'basic') {
            return response;
          }
          
          // Clone the response because it's a stream
          const responseToCache = response.clone();
          
          caches.open(CACHE_NAME)
            .then((cache) => {
              cache.put(event.request, responseToCache);
            });
          
          return response;
        });
      })
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// Handle background sync for countdown updates (optional enhancement)
self.addEventListener('sync', (event) => {
  if (event.tag === 'countdown-sync') {
    event.waitUntil(
      // You could add logic here to sync countdown data
      // For now, just log the sync event
      console.log('Background sync triggered for countdown')
    );
  }
});

// Push notification support (for future enhancements)
self.addEventListener('push', (event) => {
  if (event.data) {
    const data = event.data.json();
    
    const options = {
      body: data.body || 'The countdown continues...',
      icon: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"%3E%3Cdefs%3E%3ClinearGradient id="grad" x1="0%25" y1="0%25" x2="100%25" y2="100%25"%3E%3Cstop offset="0%25" stop-color="%23533483"/%3E%3Cstop offset="50%25" stop-color="%230f3460"/%3E%3Cstop offset="100%25" stop-color="%231a0b2e"/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width="192" height="192" fill="url(%23grad)" rx="20"/%3E%3Ctext x="96" y="80" text-anchor="middle" fill="white" font-size="48" font-weight="bold" font-family="Arial"%3EOCT%3C/text%3E%3Ctext x="96" y="140" text-anchor="middle" fill="white" font-size="72" font-weight="bold" font-family="Arial"%3E01%3C/text%3E%3C/svg%3E',
      badge: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"%3E%3Ccircle cx="48" cy="48" r="48" fill="%231a0b2e"/%3E%3Ctext x="48" y="60" text-anchor="middle" fill="white" font-size="36" font-weight="bold"%3E1%3C/text%3E%3C/svg%3E',
      vibrate: [200, 100, 200],
      tag: 'countdown-notification',
      requireInteraction: false,
      data: data
    };
    
    event.waitUntil(
      self.registration.showNotification(data.title || 'October 1st Countdown', options)
    );
  }
});

// Handle notification clicks
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  
  event.waitUntil(
    clients.openWindow('/')
  );
});