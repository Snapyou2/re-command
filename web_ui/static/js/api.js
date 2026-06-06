const API = {
    async fetchConfig() {
        const response = await fetch('/api/config');
        return response.json();
    },

    async updateConfig(config) {
        const response = await fetch('/api/update_config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        return response.json();
    },

    async getDownloadQueue() {
        const response = await fetch('/api/download_queue');
        return response.json();
    },

    async getFreshReleases() {
        const response = await fetch('/api/get_fresh_releases');
        return response.json();
    },

    async getListenBrainzPlaylist() {
        const response = await fetch('/api/get_listenbrainz_playlist');
        return response.json();
    },

    async triggerListenBrainzDownload() {
        const response = await fetch('/api/trigger_listenbrainz_download', { method: 'POST' });
        return response.json();
    },

    async getLastFmPlaylist() {
        const response = await fetch('/api/get_lastfm_playlist');
        return response.json();
    },

    async triggerLastFmDownload() {
        const response = await fetch('/api/trigger_lastfm_download', { method: 'POST' });
        return response.json();
    },

    async getLlmPlaylist(signal) {
        const response = await fetch('/api/get_llm_playlist', { signal });
        return response.json();
    },

    async triggerLlmDownload() {
        const response = await fetch('/api/trigger_llm_download', { method: 'POST' });
        return response.json();
    },

    async triggerTrackDownload(artist, title, lbRecommendation = false, source = 'Manual') {
        const response = await fetch('/api/trigger_track_download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist, title, lb_recommendation: lbRecommendation, source })
        });
        return response.json();
    },

    async triggerFreshReleaseDownload(artist, album, releaseDate, isAlbumRecommendation = false) {
        const response = await fetch('/api/trigger_fresh_release_download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                artist,
                album,
                release_date: releaseDate,
                is_album_recommendation: isAlbumRecommendation
            })
        });
        return response.json();
    },

    async downloadFromLink(link) {
        const response = await fetch('/api/download_from_link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ link })
        });
        return response.json();
    },

    async submitListenBrainzFeedback(recordingMbid, score) {
        const response = await fetch('/api/submit_listenbrainz_feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ recording_mbid: recordingMbid, score })
        });
        return response.json();
    },

    async submitLastFmFeedback(track, artist) {
        const response = await fetch('/api/submit_lastfm_feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track, artist })
        });
        return response.json();
    },

    async getTrackPreview(artist, title) {
        const response = await fetch(`/api/get_track_preview?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}`);
        return response.json();
    },

    async getDeezerAlbumArt(artist, albumTitle) {
        const response = await fetch(`/api/get_deezer_album_art?artist=${encodeURIComponent(artist)}&album_title=${encodeURIComponent(albumTitle)}`);
        return response.json();
    },

    async updateCron(schedule) {
        const response = await fetch('/api/update_cron', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ schedule })
        });
        return response.json();
    },

    async toggleCron(disabled) {
        const response = await fetch('/api/toggle_cron', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ disabled })
        });
        return response.json();
    },

    async triggerNavidromeCleanup() {
        const response = await fetch('/api/trigger_navidrome_cleanup', { method: 'POST' });
        return response.json();
    },

    async createSmartPlaylists() {
        const response = await fetch('/api/create_smart_playlists', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        return response.json();
    },

    async getLastFmAuthUrl() {
        const response = await fetch('/api/get_lastfm_auth_url');
        return response.json();
    },

    async clearLastFmAuthUrl() {
        const response = await fetch('/api/clear_lastfm_auth_url', { method: 'POST' });
        return response.json();
    },

    async cancelDownload(downloadId) {
        const response = await fetch('/api/cancel_download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ download_id: downloadId })
        });
        return response.json();
    },

    async flushQueue() {
        const response = await fetch('/api/flush_queue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        return response.json();
    }
};