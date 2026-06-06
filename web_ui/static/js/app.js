document.addEventListener('alpine:init', () => {
    Alpine.data('app', () => ({
        config: null,
        downloadQueue: [],
        listenBrainzPlaylist: [],
        lastFmPlaylist: [],
        llmPlaylist: [],
        freshReleases: [],
        isQueuePanelOpen: false,
        isSettingsModalOpen: false,
        isLoading: false,
        currentAudio: null,
        llmAbortController: null,
        albumRecommendationEnabled: false,
        collapsedSections: {},
        hasAnyConfig: false,
        showListenBrainz: false,
        showFreshReleases: false,
        showLastFm: false,
        showLlm: false,
        showDownloadFromLink: false,
        listenbrainzOptionsVisible: false,
        lastfmOptionsVisible: false,
        llmOptionsVisible: false,
        maxConcurrentDownloads: 1,
        activeDownloadCount: 0,
        pendingDownloads: new Set(),
        queuePollInterval: null,
        currentPollInterval: 3000,
        toasts: [],
        queuedCount: 0,
        lastfmAuthUrl: null,

        async init() {
            await this.loadConfig();
            try {
                await API.flushQueue();
            } catch (e) {
                console.error('Error flushing queue:', e);
            }
            if (this.config && this.config.LISTENBRAINZ_ENABLED) {
                this.fetchFreshReleases();
            }
            this.maxConcurrentDownloads = this.config?.max_concurrent_downloads || 1;
            this.startQueuePolling();
            this.startLastFmAuthPolling();
            this.setupEventListeners();
        },

        setupEventListeners() {
            document.addEventListener('open-queue-panel', () => this.toggleQueuePanel());
            document.addEventListener('close-queue-panel', () => this.closeQueuePanel());
        },

        async loadConfig() {
            try {
                this.config = await API.fetchConfig();
                this.albumRecommendationEnabled = this.config.ALBUM_RECOMMENDATION_ENABLED || false;
                this.updateUIVisibility();
            } catch (error) {
                this.showToast('error', 'Failed to load configuration');
            }
        },

        updateUIVisibility() {
            if (!this.config) return;
            const hasNavidrome = (this.config.ROOT_ND && this.config.ROOT_ND !== '') ||
                (this.config.USER_ND && this.config.USER_ND !== '') ||
                (this.config.PASSWORD_ND && this.config.PASSWORD_ND !== '');
            const hasAnyConfig = hasNavidrome || (this.config.DEEZER_ARL && this.config.DEEZER_ARL !== '');
            this.hasAnyConfig = hasAnyConfig;
            this.showListenBrainz = this.config.LISTENBRAINZ_ENABLED;
            this.showFreshReleases = this.config.LISTENBRAINZ_ENABLED && !this.config.HIDE_FRESH_RELEASES;
            this.showLastFm = this.config.LASTFM_ENABLED;
            this.showLlm = this.config.LLM_ENABLED;
            this.showDownloadFromLink = hasAnyConfig && !this.config.HIDE_DOWNLOAD_FROM_LINK;
            this.listenbrainzOptionsVisible = this.config.LISTENBRAINZ_ENABLED;
            this.lastfmOptionsVisible = this.config.LASTFM_ENABLED;
            this.llmOptionsVisible = this.config.LLM_ENABLED;
            if (this.config.max_concurrent_downloads) {
                this.maxConcurrentDownloads = this.config.max_concurrent_downloads;
            }
        },

        startQueuePolling() {
            this.queuePollInterval = setInterval(async () => {
                await this.fetchDownloadQueue();
                const hasActiveDownloads = this.downloadQueue.some(i => i.status === 'in_progress');
                const hasPendingDownloads = this.downloadQueue.some(i => i.status === 'pending');
                let targetInterval = 5000;
                if (hasActiveDownloads || hasPendingDownloads) {
                    targetInterval = 1500;
                } else if (this.downloadQueue.length > 0) {
                    targetInterval = 3000;
                }
                if (targetInterval !== this.currentPollInterval) {
                    this.stopQueuePolling();
                    this.currentPollInterval = targetInterval;
                    this.startQueuePolling();
                }
            }, this.currentPollInterval);
        },

        stopQueuePolling() {
            if (this.queuePollInterval) {
                clearInterval(this.queuePollInterval);
                this.queuePollInterval = null;
            }
        },

        startLastFmAuthPolling() {
            this.lastfmAuthCheckInterval = setInterval(async () => {
                if (this.config && this.config.LASTFM_ENABLED && !this.lastfmAuthUrl) {
                    try {
                        const data = await API.getLastFmAuthUrl();
                        if (data.status === 'success' && data.auth_url) {
                            this.lastfmAuthUrl = data.auth_url;
                        }
                    } catch (error) {
                    }
                }
            }, 5000);
        },

        async dismissLastFmAuth() {
            this.lastfmAuthUrl = null;
            try {
                await API.clearLastFmAuthUrl();
            } catch (error) {
                console.error('Error clearing Last.fm auth URL:', error);
            }
        },

        async copyLastFmAuthUrl() {
            if (this.lastfmAuthUrl) {
                try {
                    await navigator.clipboard.writeText(this.lastfmAuthUrl);
                    this.showToast('success', 'Last.fm auth URL copied to clipboard!');
                } catch (error) {
                    console.error('Error copying to clipboard:', error);
                    this.showToast('error', 'Failed to copy URL to clipboard');
                }
            }
        },

        async cancelDownload(downloadId) {
            try {
                const data = await API.cancelDownload(downloadId);
                if (data.status === 'success') {
                    this.showToast('success', 'Download cancelled successfully');
                    await this.fetchDownloadQueue();
                } else {
                    this.showToast('error', data.message || 'Failed to cancel download');
                }
            } catch (error) {
                console.error('Error cancelling download:', error);
                this.showToast('error', 'Failed to cancel download');
            }
        },

        async fetchDownloadQueue() {
            try {
                const data = await API.getDownloadQueue();
                if (data.status === 'success') {
                    this.downloadQueue = data.queue.map(item => ({ ...item }));
                    this.activeDownloadCount = data.active_download_count || 0;
                    this.queuedCount = this.downloadQueue.filter(item => item.status === 'queued').length;
                }
            } catch (error) {
                console.error('Error fetching download queue:', error);
            }
        },

        toggleQueuePanel() {
            this.isQueuePanelOpen = !this.isQueuePanelOpen;
            document.body.classList.toggle('download-queue-panel-open', this.isQueuePanelOpen);
            if (this.isQueuePanelOpen) {
                this.fetchDownloadQueue();
            }
        },

        closeQueuePanel() {
            this.isQueuePanelOpen = false;
            document.body.classList.remove('download-queue-panel-open');
        },

        openSettingsModal() {
            this.isSettingsModalOpen = true;
        },

        closeSettingsModal() {
            this.isSettingsModalOpen = false;
        },

        toggleSection(section) {
            this.collapsedSections[section] = !this.collapsedSections[section];
        },

        isSectionCollapsed(section) {
            return this.collapsedSections[section] || false;
        },

        showToast(type, message, duration = 4000) {
            const toast = {
                id: Date.now() + Math.random(),
                type,
                message,
                show: true
            };
            this.toasts.push(toast);
            setTimeout(() => {
                this.removeToast(toast.id);
            }, duration);
        },

        removeToast(id) {
            const toast = this.toasts.find(t => t.id === id);
            if (toast) {
                toast.show = false;
                setTimeout(() => {
                    this.toasts = this.toasts.filter(t => t.id !== id);
                }, 300);
            }
        },

        async saveConfiguration() {
            try {
                const config = {
                    ROOT_ND: document.getElementById('navidromeUrl').value.trim(),
                    USER_ND: document.getElementById('navidromeUser').value.trim(),
                    PASSWORD_ND: document.getElementById('navidromePassword').value.trim(),
                    LISTENBRAINZ_ENABLED: document.getElementById('listenbrainzEnabled').checked,
                    TOKEN_LB: document.getElementById('listenbrainzToken').value.trim(),
                    USER_LB: document.getElementById('listenbrainzUser').value.trim(),
                    LASTFM_ENABLED: document.getElementById('lastfmEnabled').checked,
                    LASTFM_API_KEY: document.getElementById('lastfmApiKey').value.trim(),
                    LASTFM_API_SECRET: document.getElementById('lastfmApiSecret').value.trim(),
                    LASTFM_USERNAME: document.getElementById('lastfmUsername').value.trim(),
                    LASTFM_PASSWORD: document.getElementById('lastfmPassword').value.trim(),
                    DEEZER_ARL: document.getElementById('deezerArl').value.trim(),
                    DOWNLOAD_METHOD: document.getElementById('downloadMethod').value,
                    ALBUM_RECOMMENDATION_ENABLED: document.getElementById('albumRecommendationEnabled').checked,
                    HIDE_DOWNLOAD_FROM_LINK: document.getElementById('hideDownloadFromLink').checked,
                    HIDE_FRESH_RELEASES: document.getElementById('hideFreshReleases').checked,
                    LLM_ENABLED: document.getElementById('llmEnabled').checked,
                    LLM_PROVIDER: document.getElementById('llmProvider').value,
                    LLM_MODEL_NAME: document.getElementById('llmModelName').value.trim(),
                    LLM_API_KEY: document.getElementById('llmApiKey').value.trim(),
                    LLM_BASE_URL: document.getElementById('llmBaseUrl').value.trim()
                };
                const data = await API.updateConfig(config);
                if (data.status === 'success') {
                    this.config = { ...this.config, ...config };
                    this.updateUIVisibility();
                    this.closeSettingsModal();
                    this.showToast('success', 'Configuration saved successfully!');
                    await this.refreshEnabledProviders();
                } else {
                    this.showToast(data.status, data.message);
                }
            } catch (error) {
                console.error('Save configuration error:', error);
                this.showToast('error', 'Failed to save configuration: ' + error.message);
            }
        },

        async saveAutomationOptions() {
            const hour = document.getElementById('cronHour').value;
            const day = document.getElementById('cronDay').value;
            const disabled = document.getElementById('disableCron').checked;
            const albumRecommendationEnabled = document.getElementById('albumRecommendationEnabled').checked;
            const schedule = `0 ${hour} * * ${day}`;
            try {
                const cronData = await API.updateCron(schedule);
                const disableData = await API.toggleCron(disabled);
                const config = { ALBUM_RECOMMENDATION_ENABLED: albumRecommendationEnabled };
                const configData = await API.updateConfig(config);
                if (cronData.status === 'success' && disableData.status === 'success' && configData.status === 'success') {
                    this.albumRecommendationEnabled = albumRecommendationEnabled;
                    this.showToast('success', 'Automation options saved successfully!');
                } else {
                    const errors = [];
                    if (cronData.status !== 'success') errors.push(`Cron: ${cronData.message}`);
                    if (disableData.status !== 'success') errors.push(`Disable: ${disableData.message}`);
                    if (configData.status !== 'success') errors.push(`Config: ${configData.message}`);
                    this.showToast('error', `Failed to save: ${errors.join(', ')}`);
                }
            } catch (error) {
                this.showToast('error', 'Failed to save automation options');
            }
        },

        async refreshEnabledProviders() {
            if (this.config.LISTENBRAINZ_ENABLED && this.freshReleases.length === 0) {
                this.fetchFreshReleases();
            }
        },

        async fetchFreshReleases() {
            try {
                const data = await API.getFreshReleases();
                if (data.status === 'success') {
                    this.freshReleases = data.releases || [];
                    this.$nextTick(() => this.lazyLoadAlbumArts('freshReleasesPlaylist'));
                }
            } catch (error) {
                console.error('Error fetching fresh releases:', error);
            }
        },

        async discoverListenBrainzPlaylist() {
            this.isLoading = true;
            this.listenBrainzPlaylist = [];
            try {
                const data = await API.getListenBrainzPlaylist();
                if (data.status === 'success') {
                    this.listenBrainzPlaylist = data.recommendations || [];
                    this.$nextTick(() => this.lazyLoadAlbumArts('listenBrainzPlaylist'));
                    this.showToast('success', `Loaded ${data.recommendations.length} tracks from ListenBrainz`);
                } else {
                    this.showToast(data.status, data.message);
                }
            } catch (error) {
                this.showToast('error', `Network error: ${error.message}`);
            } finally {
                this.isLoading = false;
            }
        },

        async discoverLastFmPlaylist() {
            this.isLoading = true;
            this.lastFmPlaylist = [];
            try {
                const data = await API.getLastFmPlaylist();
                if (data.status === 'success') {
                    this.lastFmPlaylist = data.recommendations || [];
                    this.showToast('success', `Loaded ${data.recommendations.length} tracks from Last.fm`);
                } else {
                    this.showToast(data.status, data.message);
                }
            } catch (error) {
                this.showToast('error', `Network error: ${error.message}`);
            } finally {
                this.isLoading = false;
            }
        },

        async discoverLlmPlaylist() {
            if (this.llmAbortController) {
                this.llmAbortController.abort();
            }
            this.llmAbortController = new AbortController();
            this.isLoading = true;
            this.llmPlaylist = [];
            try {
                const data = await API.getLlmPlaylist(this.llmAbortController.signal);
                if (data.status === 'success') {
                    this.llmPlaylist = data.recommendations || [];
                    this.$nextTick(() => this.lazyLoadAlbumArts('llmPlaylist'));
                    this.showToast('success', `LLM generated ${data.recommendations.length} recommendations`);
                } else {
                    this.showToast(data.status, data.message);
                }
            } catch (error) {
                if (error.name === 'AbortError') {
                    this.showToast('info', 'LLM query cancelled');
                } else {
                    this.showToast('error', `Network error: ${error.message}`);
                }
            } finally {
                this.isLoading = false;
            }
        },

        cancelLlmQuery() {
            if (this.llmAbortController) {
                this.llmAbortController.abort();
                this.showToast('info', 'LLM query cancelled');
            }
        },

        get canStartDownload() {
            return this.activeDownloadCount < this.maxConcurrentDownloads;
        },

        async triggerListenBrainzDownload() {
            const data = await API.triggerListenBrainzDownload();
            this.showToast(data.status, data.message);
            if (data.status === 'error') {
                this.activeDownloadCount = data.active_count || this.activeDownloadCount;
            } else {
                this.isQueuePanelOpen = true;
                document.body.classList.add('download-queue-panel-open');
                if (data.queued) {
                    this.fetchDownloadQueue();
                } else {
                    this.stopQueuePolling();
                    this.currentPollInterval = 1500;
                    this.startQueuePolling();
                }
            }
        },

        async triggerLastFmDownload() {
            const data = await API.triggerLastFmDownload();
            this.showToast(data.status, data.message);
            if (data.status === 'error') {
                this.activeDownloadCount = data.active_count || this.activeDownloadCount;
            } else {
                this.isQueuePanelOpen = true;
                document.body.classList.add('download-queue-panel-open');
                if (data.queued) {
                    this.fetchDownloadQueue();
                } else {
                    this.stopQueuePolling();
                    this.currentPollInterval = 1500;
                    this.startQueuePolling();
                }
            }
        },

        async triggerLlmDownload() {
            const data = await API.triggerLlmDownload();
            this.showToast(data.status, data.message);
            if (data.status === 'error') {
                this.activeDownloadCount = data.active_count || this.activeDownloadCount;
            } else {
                this.isQueuePanelOpen = true;
                document.body.classList.add('download-queue-panel-open');
                if (data.queued) {
                    this.fetchDownloadQueue();
                } else {
                    this.stopQueuePolling();
                    this.currentPollInterval = 1500;
                    this.startQueuePolling();
                }
            }
        },

        async downloadFreshRelease(artist, album, releaseDate) {
            const data = await API.triggerFreshReleaseDownload(
                artist, album, releaseDate, this.albumRecommendationEnabled
            );
            this.showToast(data.status, data.message);
            if (data.status === 'error') {
                this.activeDownloadCount = data.active_count || this.activeDownloadCount;
            } else {
                this.isQueuePanelOpen = true;
                document.body.classList.add('download-queue-panel-open');
                if (data.queued) {
                    this.fetchDownloadQueue();
                } else {
                    this.stopQueuePolling();
                    this.currentPollInterval = 1500;
                    this.startQueuePolling();
                }
            }
        },

        async downloadFromLink(link) {
            if (!link || !link.trim()) {
                this.showToast('error', 'Please paste a music link to download.');
                return;
            }
            const tempId = 'temp-link-' + Date.now();
            this.downloadQueue.unshift({
                id: tempId,
                artist: 'Link Download',
                title: link.substring(0, 50) + (link.length > 50 ? '...' : ''),
                status: 'pending',
                start_time: new Date().toISOString(),
                message: 'Processing link...'
            });
            this.isQueuePanelOpen = true;
            document.body.classList.add('download-queue-panel-open');
            this.activeDownloadCount++;
            const data = await API.downloadFromLink(link);
            this.showToast(data.status, data.message);
            if (data.status === 'error') {
                this.activeDownloadCount = data.active_count || this.activeDownloadCount;
                this.downloadQueue = this.downloadQueue.filter(item => item.id !== tempId);
            } else {
                const item = this.downloadQueue.find(i => i.id === tempId);
                if (item) item.status = 'in_progress';
                this.stopQueuePolling();
                this.currentPollInterval = 1500;
                this.startQueuePolling();
            }
        },

        async downloadTrack(artist, title, lbRecommendation = false, source = 'Manual') {
            const data = await API.triggerTrackDownload(artist, title, lbRecommendation, source);
            this.showToast(data.status, data.message);
            if (data.status === 'error') {
                this.activeDownloadCount = data.active_count || this.activeDownloadCount;
            } else {
                this.isQueuePanelOpen = true;
                document.body.classList.add('download-queue-panel-open');
                if (data.queued) {
                    this.fetchDownloadQueue();
                } else {
                    this.stopQueuePolling();
                    this.currentPollInterval = 1500;
                    this.startQueuePolling();
                }
            }
        },

        async playTrackPreview(artist, title, button) {
            if (button.classList.contains('playing') && this.currentAudio) {
                this.currentAudio.pause();
                this.currentAudio.currentTime = 0;
                this.currentAudio = null;
                button.classList.remove('playing');
                const icon = button.querySelector('.feedback-icon path');
                if (icon) icon.setAttribute('d', 'M5 3L19 12L5 21V3Z');
                return;
            }
            if (this.currentAudio) {
                this.currentAudio.pause();
                this.currentAudio.currentTime = 0;
                const prevBtn = document.querySelector('.play-btn.playing');
                if (prevBtn) {
                    prevBtn.classList.remove('playing');
                    const prevIcon = prevBtn.querySelector('.feedback-icon path');
                    if (prevIcon) prevIcon.setAttribute('d', 'M5 3L19 12L5 21V3Z');
                }
            }
            try {
                const data = await API.getTrackPreview(artist, title);
                if (data.status === 'success' && data.preview_url) {
                    this.currentAudio = new Audio(data.preview_url);
                    button.classList.add('playing');
                    const icon = button.querySelector('.feedback-icon path');
                    if (icon) icon.setAttribute('d', 'M6 4H18V20H6V4Z');
                    this.currentAudio.addEventListener('ended', () => {
                        button.classList.remove('playing');
                        const icon = button.querySelector('.feedback-icon path');
                        if (icon) icon.setAttribute('d', 'M5 3L19 12L5 21V3Z');
                        this.currentAudio = null;
                    });
                    this.currentAudio.addEventListener('error', () => {
                        this.showToast('error', 'Failed to play preview');
                        button.classList.remove('playing');
                        const icon = button.querySelector('.feedback-icon path');
                        if (icon) icon.setAttribute('d', 'M5 3L19 12L5 21V3Z');
                        this.currentAudio = null;
                    });
                    await this.currentAudio.play();
                } else {
                    this.showToast('error', data.message || 'Preview not available');
                }
            } catch (error) {
                this.showToast('error', 'Failed to play preview');
            }
        },

        async submitFeedback(type, ...args) {
            try {
                let data;
                if (type === 'listenbrainz') {
                    data = await API.submitListenBrainzFeedback(...args);
                } else if (type === 'lastfm') {
                    data = await API.submitLastFmFeedback(...args);
                }
                this.showToast(data.status, data.message);
            } catch (error) {
                this.showToast('error', 'Failed to submit feedback');
            }
        },

        async triggerNavidromeCleanup() {
            const data = await API.triggerNavidromeCleanup();
            this.showToast(data.status, data.message);
        },

        async createSmartPlaylists() {
            const data = await API.createSmartPlaylists();
            this.showToast(data.status, data.message);
        },

        scrollCarousel(direction) {
            const carousel = document.getElementById('freshReleasesCarousel');
            if (carousel) {
                carousel.scrollBy({ left: direction * 220, behavior: 'smooth' });
            }
        },

        lazyLoadAlbumArts(containerId) {
            const container = document.getElementById(containerId);
            if (!container) return;
            const images = container.querySelectorAll('.album-art, .release-art');
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const img = entry.target;
                        this.loadAlbumArt(img);
                        observer.unobserve(img);
                    }
                });
            }, { threshold: 0.1 });
            images.forEach(img => observer.observe(img));
        },

        async loadAlbumArt(img) {
            const caaReleaseMbid = img.dataset.caaReleaseMbid;
            const caaId = img.dataset.caaId;
            let imageUrl = '/assets/default-album.svg';
            if (caaReleaseMbid && caaId) {
                imageUrl = `http://coverartarchive.org/release/${caaReleaseMbid}/${caaId}-250.jpg`;
            } else if (caaReleaseMbid) {
                imageUrl = `https://coverartarchive.org/release/${caaReleaseMbid}/front-250.jpg`;
            }
            const spinner = img.nextElementSibling;
            if (spinner) spinner.style.display = 'block';
            img.style.opacity = '0';
            const tempImg = new Image();
            tempImg.onload = () => {
                img.src = imageUrl;
                img.classList.add('loaded');
                img.style.opacity = '1';
                if (spinner) spinner.style.display = 'none';
            };
            tempImg.onerror = async () => {
                const artist = img.dataset.artist;
                const album = img.dataset.album;
                if (artist && album) {
                    try {
                        const data = await API.getDeezerAlbumArt(artist, album);
                        if (data.status === 'success' && data.album_art_url) {
                            img.src = data.album_art_url;
                            img.onload = () => {
                                img.classList.add('loaded');
                                img.style.opacity = '1';
                                if (spinner) spinner.style.display = 'none';
                            };
                        } else {
                            img.src = '/assets/default-album.svg';
                            img.classList.add('loaded');
                            img.style.opacity = '1';
                            if (spinner) spinner.style.display = 'none';
                        }
                    } catch {
                        img.src = '/assets/default-album.svg';
                        img.classList.add('loaded');
                        img.style.opacity = '1';
                        if (spinner) spinner.style.display = 'none';
                    }
                } else {
                    img.src = '/assets/default-album.svg';
                    img.classList.add('loaded');
                    img.style.opacity = '1';
                    if (spinner) spinner.style.display = 'none';
                }
            };
            tempImg.src = imageUrl;
        }
    }));
});
