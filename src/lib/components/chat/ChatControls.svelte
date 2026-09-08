<script context="module" lang="ts">
	let savedTab: 'controls' | 'overview' = 'controls';
</script>

<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';
	import { showControls, showEmbeds, user } from '$lib/stores';

	import Controls from './Controls/Controls.svelte';
	import Drawer from '../common/Drawer.svelte';
	import ResizableSidePanel from '../common/ResizableSidePanel.svelte';
	import Embeds from './ChatControls/Embeds.svelte';
	import Overview from './Overview.svelte';

	const i18n = getContext('i18n');

	export let history;
	export let models = [];

	export let chatId = null;
	export let chatUser = null;

	export let chatFiles = [];
	export let params = {};

	export let showMessage: Function;
	export let files;

	let largeScreen = false;
	let dragged = false;
	let mounted = false;
	let controlsWidth = 350;

	// Tab state for Controls+Overview panel
	let activeTab = savedTab;
	// svelte-ignore reactive_declaration_module_script_dependency
	$: {
		savedTab = activeTab;
	}

	$: hasMessages = history?.messages && Object.keys(history.messages).length > 0;

	$: showControlsTab = $user?.role === 'admin' || ($user?.permissions?.chat?.controls ?? true);
	$: showOverviewTab = hasMessages;

	// Tab fallback: if active tab becomes hidden, switch to next available
	$: if (!showOverviewTab && activeTab === 'overview') activeTab = 'controls';
	$: if (!showControlsTab && activeTab === 'controls') {
		if (showOverviewTab) activeTab = 'overview';
	}

	// Auto-close if there are no visible tabs
	$: if (!showControlsTab && !showOverviewTab) {
		showControls.set(false);
	}

	const handleMediaQuery = (e) => {
		largeScreen = e.matches;
	};

	const onMouseDown = () => {
		dragged = true;
	};
	const onMouseUp = () => {
		dragged = false;
	};

	onMount(() => {
		const mediaQuery = window.matchMedia('(min-width: 1024px)');
		mediaQuery.addEventListener('change', handleMediaQuery);
		handleMediaQuery(mediaQuery);

		let isDestroyed = false;

		const init = async () => {
			await tick();

			if (isDestroyed) return;

			setTimeout(() => {
				mounted = true;
			}, 0);
		};
		init();

		document.addEventListener('mousedown', onMouseDown);
		document.addEventListener('mouseup', onMouseUp);

		return () => {
			isDestroyed = true;
			mounted = false;
			if (!largeScreen) {
				showControls.set(false);
			}
			mediaQuery.removeEventListener('change', handleMediaQuery);
			document.removeEventListener('mousedown', onMouseDown);
			document.removeEventListener('mouseup', onMouseUp);
		};
	});

	const closeHandler = () => {
		if (!largeScreen) {
			showControls.set(false);
		}
		showEmbeds.set(false);
	};

	$: if (mounted && !chatId) closeHandler();
</script>

{#if !largeScreen}
	{#if $showControls}
		<Drawer
			show={$showControls}
			onClose={() => showControls.set(false)}
			className="min-h-[100dvh] !bg-white dark:!bg-gray-850"
		>
			<div class="h-[100dvh] flex flex-col">
				{#if $showEmbeds}
					<Embeds />
				{:else}
					<!-- Controls + Overview tabs -->
					<div class="flex flex-col h-full min-h-0">
						<!-- Tab bar -->
						<div class="flex items-center justify-between px-2 pt-2 pb-2 shrink-0">
							<div class="flex gap-1 min-w-0 overflow-x-auto scrollbar-hidden">
								{#if showControlsTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'controls'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'controls')}
									>
										{$i18n.t('Controls')}
									</button>
								{/if}
								{#if showOverviewTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'overview'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'overview')}
									>
										{$i18n.t('Overview')}
									</button>
								{/if}
							</div>
							<button
								class="p-1 rounded-lg text-gray-500 dark:text-gray-400"
								on:click={() => showControls.set(false)}
								aria-label={$i18n.t('Close')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="1.5"
									class="size-4"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
								</svg>
							</button>
						</div>

						<div
							class="flex-1 min-h-0 {activeTab === 'overview'
								? 'h-full'
								: 'overflow-y-auto px-3 pt-1'}"
						>
							{#if activeTab === 'overview'}
								<Overview
									{history}
									{chatUser}
									onNodeClick={(e) => {
										const node = e.node;
										showMessage(node.data.message, true);
									}}
								/>
							{:else}
								<Controls embed={true} {models} bind:chatFiles bind:params />
							{/if}
						</div>
					</div>
				{/if}
			</div>
		</Drawer>
	{/if}
{:else}
	<ResizableSidePanel
		open={$showControls}
		bind:width={controlsWidth}
		minWidth={350}
		minSiblingWidth={360}
		closeOnDragBelowMinWidth
		onClose={() => showControls.set(false)}
		storageKey="chatControlsSize"
		className="h-full z-10 bg-white dark:bg-gray-900"
	>
		<div class="flex h-full max-h-full min-h-full">
			<div
				class="w-full {$showEmbeds ? ' ' : 'bg-white dark:bg-gray-900'} z-40 pointer-events-auto overflow-y-auto scrollbar-hidden"
				id="controls-container"
			>
				{#if $showEmbeds}
					<Embeds overlay={dragged} />
				{:else}
					<!-- Controls + Overview tabs -->
					<div class="flex flex-col h-full min-h-0">
						<!-- Tab bar -->
						<div class="flex items-center justify-between px-2 pt-2 pb-2 shrink-0">
							<div class="flex gap-1 min-w-0 overflow-x-auto scrollbar-hidden">
								{#if showControlsTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'controls'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'controls')}
									>
										{$i18n.t('Controls')}
									</button>
								{/if}
								{#if showOverviewTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'overview'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'overview')}
									>
										{$i18n.t('Overview')}
									</button>
								{/if}
							</div>
							<button
								class="p-1 rounded-lg text-gray-500 dark:text-gray-400"
								on:click={() => showControls.set(false)}
								aria-label={$i18n.t('Close')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="1.5"
									class="size-4"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
								</svg>
							</button>
						</div>

						<div
							class="flex-1 min-h-0 {activeTab === 'overview'
								? 'h-full'
								: 'overflow-y-auto px-3 pt-1'}"
						>
							{#if activeTab === 'overview'}
								<Overview
									{history}
									{chatUser}
									onNodeClick={(e) => {
										const node = e.node;
										if (node?.data?.message?.favorite) {
											history.messages[node.data.message.id].favorite = true;
										} else {
											history.messages[node.data.message.id].favorite = null;
										}
										showMessage(node.data.message, true);
									}}
								/>
							{:else}
								<Controls embed={true} {models} bind:chatFiles bind:params />
							{/if}
						</div>
					</div>
				{/if}
			</div>
		</div>
	</ResizableSidePanel>
{/if}
