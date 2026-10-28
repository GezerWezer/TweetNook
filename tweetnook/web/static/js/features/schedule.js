/**
 * Settings → Schedule: the automatic sync's interval or day, time, time zone and random
 * delay window, and saving them.
 */

function scheduleMethods() {
    return {
        async fetchScheduleSettings() {
            this.scheduleError = '';
            try {
                const response = await fetch('/api/activity/schedule');
                if (!response.ok) throw new Error('Could not load schedule');
                const data = await response.json();
                this.scheduleSettings = data;
                this.scheduleForm = this.normalizeScheduleConfig(data.config);
                this.syncScheduleTimePicker();
                this.scheduleOriginal = JSON.parse(JSON.stringify(this.scheduleForm));
                this.scheduleSaved = false;
            } catch (error) {
                this.scheduleError = error.message;
                this.scheduleSettings = null;
            }
        },

        normalizeScheduleConfig(config = {}) {
            return {
                enabled: !!config.enabled,
                cadence: ['hours', 'daily', 'weekly', 'monthly'].includes(config.cadence)
                    ? config.cadence
                    : 'daily',
                every_hours: Number(config.every_hours ?? 6),
                time: config.time || '03:00',
                randomize_time: !!(config.randomize_time ?? true),
                random_offset_min_hours: Number(config.random_offset_min_hours ?? 0),
                random_offset_max_hours: Number(config.random_offset_max_hours ?? 2),
                weekday: Number(config.weekday ?? 0),
                day_of_month: Number(config.day_of_month ?? 1),
                timezone: config.timezone || 'local',
            };
        },

        scheduleTimezoneOptions() {
            const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
            const current = this.scheduleForm?.timezone;
            const values = [
                { value: 'local', label: 'System local time' },
                { value: 'UTC', label: 'UTC' },
            ];
            if (detected && !values.some(option => option.value === detected)) {
                values.push({ value: detected, label: `${detected} (browser)` });
            }
            const supported = typeof Intl.supportedValuesOf === 'function'
                ? Intl.supportedValuesOf('timeZone')
                : [];
            for (const timezone of supported) {
                if (!values.some(option => option.value === timezone)) {
                    values.push({ value: timezone, label: timezone });
                }
            }
            if (current && !values.some(option => option.value === current)) {
                values.push({ value: current, label: current });
            }
            return values;
        },

        scheduleFieldChanged() {
            this.scheduleSaved = false;
            this.scheduleError = '';
        },

        syncScheduleTimePicker() {
            const [hourValue, minuteValue] = (this.scheduleForm?.time || '03:00').split(':');
            const hour24 = Number(hourValue);
            this.scheduleTimeHour = String(hour24 % 12 || 12);
            this.scheduleTimeMinute = String(Number(minuteValue || 0)).padStart(2, '0');
            this.scheduleTimePeriod = hour24 >= 12 ? 'PM' : 'AM';
        },

        setScheduleTimePart(part, value) {
            if (!this.scheduleForm) return;
            if (part === 'hour') this.scheduleTimeHour = String(value);
            if (part === 'minute') this.scheduleTimeMinute = String(value).padStart(2, '0');
            if (part === 'period') this.scheduleTimePeriod = String(value);
            let hour = Number(this.scheduleTimeHour) % 12;
            if (this.scheduleTimePeriod === 'PM') hour += 12;
            this.scheduleForm.time = `${String(hour).padStart(2, '0')}:${this.scheduleTimeMinute}`;
            this.scheduleFieldChanged();
        },

        scheduleRandomWindowValid() {
            if (
                !this.scheduleForm?.randomize_time
                || this.scheduleForm.cadence === 'hours'
            ) return true;
            const minimum = Number(this.scheduleForm.random_offset_min_hours);
            const maximum = Number(this.scheduleForm.random_offset_max_hours);
            return Number.isFinite(minimum)
                && Number.isFinite(maximum)
                && minimum >= 0
                && maximum >= 0
                && minimum <= 24
                && maximum <= 24
                && minimum <= maximum;
        },

        scheduleHasChanges() {
            return !!this.scheduleForm
                && JSON.stringify(this.scheduleForm) !== JSON.stringify(this.scheduleOriginal);
        },

        async saveScheduleSettings() {
            if (!this.scheduleForm || this.scheduleSaving || !this.scheduleRandomWindowValid()) return;
            this.scheduleSaving = true;
            this.scheduleSaved = false;
            this.scheduleError = '';
            const values = this.normalizeScheduleConfig(this.scheduleForm);
            try {
                const response = await fetch('/api/activity/schedule', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(values),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(
                        typeof data.detail === 'string'
                            ? data.detail
                            : 'Could not save schedule',
                    );
                }
                this.scheduleSettings = data;
                this.activitySchedule = data;
                this.scheduleForm = this.normalizeScheduleConfig(data.config);
                this.syncScheduleTimePicker();
                this.scheduleOriginal = JSON.parse(JSON.stringify(this.scheduleForm));
                this.scheduleSaved = true;
            } catch (error) {
                this.scheduleError = error.message;
            } finally {
                this.scheduleSaving = false;
            }
        },

        scheduleSettings: null,
        scheduleForm: null,
        scheduleOriginal: null,
        scheduleSaving: false,
        scheduleSaved: false,
        scheduleError: '',
        scheduleTimeHour: '3',
        scheduleTimeMinute: '00',
        scheduleTimePeriod: 'AM',
    };
}
