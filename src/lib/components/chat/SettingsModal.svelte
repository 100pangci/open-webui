<script lang="ts">
	import { browser } from '$app/environment';
	import { getContext, onMount, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { config, models, settings, user } from '$lib/stores';
	import type { SettingsModalRequest } from '$lib/stores';
	import { getUserSettings, updateUserSettings } from '$lib/apis/users';
	import { getModels as _getModels } from '$lib/apis';

	import Modal from '../common/Modal.svelte';
	import Account from './Settings/Account.svelte';
	import General from './Settings/General.svelte';

	import Search from '../icons/Search.svelte';
	import SettingsAlt from '../icons/SettingsAlt.svelte';
	import UserCircle from '../icons/UserCircle.svelte';
	import ChevronLeft from '../icons/ChevronLeft.svelte';

	import AdminTabIcon from '$lib/components/admin/Settings/AdminTabIcon.svelte';
	import AdminConnections from '$lib/components/admin/Settings/Connections.svelte';

	const i18n: Writable<any> = getContext('i18n');

	export let show: boolean | string | SettingsModalRequest = false;
	let modalShow = false;
	let lastShow: boolean | string | SettingsModalRequest = false;
	let personalUiSettings: Record<string, any> = {};

	const mergeUiSettings = (defaults: Record<string, any>, userSettings: Record<string, any>) => {
		const merged = { ...defaults };
		for (const [key, value] of Object.entries(userSettings)) {
			const defaultValue = merged[key];
			merged[key] =
				defaultValue &&
				value &&
				typeof defaultValue === 'object' &&
				typeof value === 'object' &&
				!Array.isArray(defaultValue) &&
				!Array.isArray(value)
					? mergeUiSettings(defaultValue, value)
					: value;
		}
		return merged;
	};

	const loadPersonalUiSettings = async () => {
		const userSettings = await getUserSettings(localStorage.token, true).catch((error) => {
			console.error(error);
			return null;
		});
		personalUiSettings =
			userSettings?.ui && typeof userSettings.ui === 'object' && !Array.isArray(userSettings.ui)
				? userSettings.ui
				: {};
	};

	$: if (show !== lastShow) {
		lastShow = show;
		if (show && typeof show === 'object') {
			selectedTab = show.tab;
			show = true;
			lastShow = true;
			modalShow = true;
			loadPersonalUiSettings();
			ensureAvailableSelectedTab();
		} else if (typeof show === 'string') {
			selectedTab = show;
			show = true;
			lastShow = true;
			modalShow = true;
			loadPersonalUiSettings();
			ensureAvailableSelectedTab();
		} else {
			modalShow = show;
			if (show) {
				loadPersonalUiSettings();
			}
			if (!show) {
				selectedTab = 'general';
			}
		}
	}

	const ensureAvailableSelectedTab = () => {
		const knownTabIds = ['general', 'account', 'admin:connections'];
		if (!knownTabIds.includes(selectedTab)) {
			selectedTab = 'general';
		}
	};

	$: if (!modalShow && show !== false) {
		show = false;
		lastShow = false;
		selectedTab = 'general';
	}

	interface SettingsTab {
		id: string;
		title: string;
		keywords: string[];
	}

	const isAdminTab = (tabId: string) => tabId.startsWith('admin:');
	const adminTabSegment = (tabId: string) => tabId.replace('admin:', '');
	const adminTabPanelId = (tabId: string) => `tab-${tabId.replace(':', '-')}`;
	const personalSettingGroups: Record<string, string> = {
		general: 'Basics',
		account: 'Profile'
	};
	const adminSettingGroups: Record<string, string> = {
		'admin:connections': 'AI'
	};
	const settingGroupTitle = (tabId: string) =>
		(isAdminTab(tabId) ? adminSettingGroups[tabId] : personalSettingGroups[tabId]) ?? 'General';
	const shouldShowSettingGroup = (tabIds: string[], index: number) =>
		index === 0 || settingGroupTitle(tabIds[index]) !== settingGroupTitle(tabIds[index - 1]);
	const settingGroupHeadingClass = (first: boolean) =>
		`hidden md:block shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600 px-2 ${
			first ? 'mt-0.5' : 'mt-2'
		} mb-0.5`;

	const allSettings: SettingsTab[] = [
		{
			id: 'general',
			title: 'General',
			keywords: [
				'advancedparams',
				'advancedparameters',
				'advanced params',
				'advanced parameters',
				'configuration',
				'defaultparameters',
				'default parameters',
				'defaultsettings',
				'default settings',
				'general',
				'keepalive',
				'keep alive',
				'languages',
				'requestmode',
				'request mode',
				'systemparameters',
				'system parameters',
				'systemprompt',
				'system prompt',
				'systemsettings',
				'system settings',
				'theme',
				'translate',
				'webuisettings',
				'webui settings'
			]
		},
		{
			id: 'account',
			title: 'Account',
			keywords: [
				'account preferences',
				'account settings',
				'accountpreferences',
				'accountsettings',
				'api keys',
				'apikeys',
				'change password',
				'changepassword',
				'jwt token',
				'jwttoken',
				'login',
				'new password',
				'newpassword',
				'notification webhook url',
				'notificationwebhookurl',
				'personal settings',
				'personalsettings',
				'privacy settings',
				'privacysettings',
				'profileavatar',
				'profile avatar',
				'profile details',
				'profile image',
				'profile picture',
				'profiledetails',
				'profileimage',
				'profilepicture',
				'security settings',
				'securitysettings',
				'update account',
				'update password',
				'updateaccount',
				'updatepassword',
				'user account',
				'user data',
				'user preferences',
				'user profile',
				'useraccount',
				'userdata',
				'username',
				'userpreferences',
				'userprofile',
				'webhook url',
				'webhookurl'
			]
		}
	];

	const adminSettings: SettingsTab[] = [
		{
			id: 'admin:connections',
			title: 'Connections',
			keywords: [
				'connections',
				'ollama',
				'openai',
				'api',
				'base url',
				'direct connections',
				'proxy'
			]
		}
	];
	let availableSettings: SettingsTab[] = [];
	let filteredSettings: string[] = [];
	let filteredPersonalSettings: string[] = [];
	let filteredAdminSettings: string[] = [];

	let search = '';
	let searchDebounceTimeout: ReturnType<typeof setTimeout> | null = null;

	const getAvailableSettings = () => {
		const personalSettings = allSettings;

		return $user?.role === 'admin' ? [...personalSettings, ...adminSettings] : personalSettings;
	};

	const setFilteredSettings = () => {
		filteredSettings = availableSettings
			.filter((tab) => {
				const query = search.toLowerCase().trim();
				return (
					query === '' ||
					tab.title.toLowerCase().includes(query) ||
					tab.keywords.some((keyword) => keyword.includes(query))
				);
			})
			.map((tab) => tab.id);
		filteredPersonalSettings = filteredSettings.filter((tabId) => !isAdminTab(tabId));
		filteredAdminSettings = filteredSettings.filter((tabId) => isAdminTab(tabId));

		if ($user?.role !== 'admin' && isAdminTab(selectedTab)) {
			selectedTab = 'general';
		} else if (filteredSettings.length > 0 && !filteredSettings.includes(selectedTab)) {
			selectedTab = filteredSettings[0];
		}

		scrollToSelectedTab();
	};

	const saveSettings = async (updated: Record<string, any>) => {
		console.log(updated);
		await settings.set({ ...$settings, ...updated });
		await models.set(await getModels());
		const saved = await updateUserSettings(localStorage.token, { ui: $settings });
		personalUiSettings =
			saved?.ui && typeof saved.ui === 'object' && !Array.isArray(saved.ui) ? saved.ui : {};
		await settings.set(
			mergeUiSettings($config?.ui?.default_interface_settings ?? {}, personalUiSettings)
		);
	};

	const getModels = async () => {
		return await _getModels(
			localStorage.token,
			$config?.features?.enable_direct_connections ? ($settings?.directConnections ?? null) : null
		);
	};

	const searchDebounceHandler = () => {
		if (searchDebounceTimeout) {
			clearTimeout(searchDebounceTimeout);
		}

		searchDebounceTimeout = setTimeout(() => {
			setFilteredSettings();
		}, 100);
	};

	const tabButtonClass = (active: boolean) =>
		`flex items-center gap-1.5 h-7 px-2 md:w-full shrink-0 rounded-lg text-xs text-left transition-colors duration-75 ${
			active
				? 'font-medium text-gray-900 dark:text-white bg-gray-50 dark:bg-white/[0.04]'
				: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
		}`;

	let selectedTab = 'general';
	const scrollToSelectedTab = async () => {
		if (!browser || !modalShow || !selectedTab) {
			return;
		}

		await tick();
		const tabElement = document.querySelector<HTMLElement>(
			'#settings-tabs-container [role="tab"][aria-selected="true"]'
		);
		tabElement?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' });
	};

	$: if ($user?.role !== 'admin' && isAdminTab(selectedTab)) {
		selectedTab = 'general';
	}

	$: if (modalShow && selectedTab) {
		scrollToSelectedTab();
	}

	onMount(() => {
		availableSettings = getAvailableSettings();
		setFilteredSettings();

		config.subscribe((configData) => {
			availableSettings = getAvailableSettings();
			setFilteredSettings();
		});
	});
</script>

<Modal
	size="full"
	containerClassName="p-4 sm:p-6 lg:p-8"
	className="!w-[calc(100vw-2rem)] sm:!w-[calc(100vw-3rem)] lg:!w-[calc(100vw-4rem)] !max-w-[80rem] h-[min(max(54rem,80dvh),calc(100dvh-4rem))] max-h-[calc(100dvh-4rem)] flex flex-col md:flex-row bg-white dark:bg-gray-900 rounded-4xl overflow-hidden"
	bind:show={modalShow}
>
	<nav
		id="settings-tabs-container"
		class="shrink-0 min-w-0 md:min-h-0 flex md:flex-col border-b md:border-b-0 md:border-r border-gray-100/30 dark:border-white/[0.02] md:w-[15rem]"
	>
		<button
			class="flex items-center gap-1.5 h-7 px-2 m-1 md:mb-0 md:w-[calc(100%-0.5rem)] shrink-0 rounded-lg text-xs text-gray-400 dark:text-gray-600 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-75"
			type="button"
			on:click={() => {
				show = false;
			}}
		>
			<ChevronLeft className="size-3" strokeWidth="2" />
			<span>{$i18n.t('Back')}</span>
		</button>

		<div
			class="hidden md:flex items-center gap-1.5 h-7 px-2 mx-1 mt-1 mb-0.5 shrink-0 rounded-lg text-xs bg-gray-50/70 dark:bg-white/[0.03]"
		>
			<div class="self-center rounded-l-xl bg-transparent">
				<Search className="size-3.5" strokeWidth="1.5" />
			</div>
			<label class="sr-only" for="search-input-settings-modal">{$i18n.t('Search')}</label>
			<input
				data-settings-search
				class="w-full text-xs bg-transparent py-1 outline-hidden dark:text-gray-300"
				bind:value={search}
				id="search-input-settings-modal"
				on:input={searchDebounceHandler}
				placeholder={$i18n.t('Search')}
			/>
		</div>

		<div
			class="tabs scrollbar-none flex min-w-0 flex-1 min-h-0 overflow-x-auto md:overflow-x-hidden md:overflow-y-auto md:flex-col p-1 pl-0 md:pl-1 gap-px"
		>
			<span
				class="hidden md:block text-[0.625rem] text-gray-400 dark:text-gray-600 px-2 mt-1.5 mb-0.5"
			>
				{$i18n.t('Personal')}
			</span>

			{#if filteredPersonalSettings.length > 0}
				{#each filteredPersonalSettings as tabId, index (tabId)}
					{#if shouldShowSettingGroup(filteredPersonalSettings, index)}
						<span class={settingGroupHeadingClass(index === 0)}>
							{$i18n.t(settingGroupTitle(tabId))}
						</span>
					{/if}

					{#if tabId === 'general'}
						<button
							role="tab"
							aria-controls="tab-general"
							aria-selected={selectedTab === 'general'}
							class={tabButtonClass(selectedTab === 'general')}
							on:click={() => {
								selectedTab = 'general';
							}}
						>
							<SettingsAlt className="size-3.5" strokeWidth="2" />
							<span>{$i18n.t('General')}</span>
						</button>
					{:else if tabId === 'account'}
						<button
							role="tab"
							aria-controls="tab-account"
							aria-selected={selectedTab === 'account'}
							class={tabButtonClass(selectedTab === 'account')}
							on:click={() => {
								selectedTab = 'account';
							}}
						>
							<UserCircle className="size-3.5" strokeWidth="2" />
							<span>{$i18n.t('Account')}</span>
						</button>
					{/if}
				{/each}
			{/if}

			{#if $user?.role === 'admin' && filteredAdminSettings.length > 0}
				<div
					class="hidden md:block shrink-0 self-stretch h-px mx-1 my-2 bg-gray-100/40 dark:bg-white/[0.025]"
				></div>
				<span class="hidden md:block text-[0.625rem] text-gray-400 dark:text-gray-600 px-2 mb-0.5">
					{$i18n.t('Admin')}
				</span>

				{#each filteredAdminSettings as tabId, index (tabId)}
					{#if shouldShowSettingGroup(filteredAdminSettings, index)}
						<span class={settingGroupHeadingClass(index === 0)}>
							{$i18n.t(settingGroupTitle(tabId))}
						</span>
					{/if}

					{@const tab = adminSettings.find((setting) => setting.id === tabId)}
					{#if tab}
						<button
							role="tab"
							aria-controls={adminTabPanelId(tab.id)}
							aria-selected={selectedTab === tab.id}
							class={tabButtonClass(selectedTab === tab.id)}
							on:click={() => {
								selectedTab = tab.id;
							}}
						>
							<AdminTabIcon id={adminTabSegment(tab.id)} className="size-3.5" strokeWidth="2" />
							<span>{$i18n.t(tab.title)}</span>
						</button>
					{/if}
				{/each}
			{/if}

			{#if filteredSettings.length === 0}
				<div class="px-2 py-1 text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t('No matches')}
				</div>
			{/if}
		</div>
	</nav>

	<div class="flex-1 min-w-0 min-h-0 p-4 md:px-5 flex flex-col">
		<div class="flex-1 min-h-0 overflow-hidden">
			{#if selectedTab === 'general'}
				<General
					{getModels}
					{saveSettings}
					on:save={() => {
						toast.success($i18n.t('Settings saved successfully!'));
					}}
				/>
			{:else if selectedTab === 'account'}
				<Account
					saveHandler={() => {
						toast.success($i18n.t('Settings saved successfully!'));
					}}
				/>
			{:else if selectedTab === 'admin:connections'}
				<AdminConnections
					on:save={() => {
						toast.success($i18n.t('Settings saved successfully!'));
					}}
				/>
			{/if}
		</div>
	</div>
</Modal>
