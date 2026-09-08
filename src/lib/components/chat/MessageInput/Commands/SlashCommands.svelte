<script lang="ts">
	import { getContext } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ChatBubbleDotted from '$lib/components/icons/ChatBubbleDotted.svelte';
	import ChatBubbleDottedChecked from '$lib/components/icons/ChatBubbleDottedChecked.svelte';
	import Knobs from '$lib/components/icons/Knobs.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';

	const i18n = getContext('i18n');

	export let query = '';
	export let onSelect = (e) => {};
	export let canCompact = false;
	export let compactDisabled = false;
	export let canStatus = false;
	export let canFork = false;
	export let forkDisabled = false;
	export let canTemporary = false;
	export let temporaryEnabled = false;
	export let contextPercent = 0;
	export let contextHasThreshold = false;

	let selectedIdx = 0;
	export let filteredItems = [];

	$: contextCirclePercent = contextHasThreshold
		? Math.min(Math.max(0, Math.round(contextPercent)), 100)
		: 0;
	$: contextCircleOffset = 50.27 * (1 - contextCirclePercent / 100);

	$: commandItems = [
		...(canTemporary && 'temporary'.startsWith(query.toLowerCase())
			? [{ type: 'command', data: { id: 'temporary' } }]
			: []),
		...(canCompact && 'compact'.startsWith(query.toLowerCase())
			? [{ type: 'command', data: { id: 'compact' } }]
			: []),
		...(canFork && 'fork'.startsWith(query.toLowerCase())
			? [{ type: 'command', data: { id: 'fork' } }]
			: []),
		...(canStatus && 'status'.startsWith(query.toLowerCase())
			? [{ type: 'command', data: { id: 'status' } }]
			: []),
		...('model'.startsWith(query.toLowerCase())
			? [{ type: 'command', data: { id: 'model' } }]
			: []),
		...('settings'.startsWith(query.toLowerCase())
			? [{ type: 'command', data: { id: 'settings' } }]
			: [])
	];

	$: filteredItems = [...commandItems];

	$: if (query) {
		selectedIdx = 0;
	}

	$: selectedIdx = Math.min(selectedIdx, Math.max(filteredItems.length - 1, 0));

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.min(selectedIdx + 1, filteredItems.length - 1);
	};

	export const select = async () => {
		const item = filteredItems[selectedIdx];
		if (item?.type === 'command' && item.data?.id === 'compact' && compactDisabled) {
			return;
		}
		if (item?.type === 'command' && item.data?.id === 'fork' && forkDisabled) {
			return;
		}
		if (item) {
			onSelect(item);
		}
	};

</script>

{#if commandItems.length > 0}
	<div class="app-muted mb-0.5 px-2 pt-1 pb-0.5 text-[0.625rem] leading-none">
		{$i18n.t('Commands')}
	</div>

	{#each commandItems as item, commandIdx}
		{#if item.data.id === 'temporary'}
			<Tooltip content="Toggle temporary chat for this new chat." placement="top">
				<button
					type="button"
					aria-label="Temporary: toggle temporary chat for this new chat."
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{commandIdx === selectedIdx ? 'app-interactive-active' : ''}"
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						onSelect(item);
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						{#if temporaryEnabled}
							<ChatBubbleDottedChecked className="size-3.5" strokeWidth="1.6" />
						{:else}
							<ChatBubbleDotted className="size-3.5" strokeWidth="1.6" />
						{/if}
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">Temporary</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							{temporaryEnabled ? 'On' : 'Off'}
						</span>
					</span>
				</button>
			</Tooltip>
		{:else if item.data.id === 'compact'}
			<Tooltip content="Shorten older messages so this chat can keep going." placement="top">
				<button
					type="button"
					aria-label="Compact: shorten older messages so this chat can keep going."
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{commandIdx === selectedIdx ? 'app-interactive-active' : ''} disabled:opacity-50"
					disabled={compactDisabled}
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						if (!compactDisabled) {
							onSelect(item);
						}
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						{#if contextHasThreshold}
							<svg class="size-3.5 -rotate-90" viewBox="0 0 20 20" aria-hidden="true">
								<circle
									cx="10"
									cy="10"
									r="8"
									fill="none"
									stroke="currentColor"
									stroke-width="2"
									class="opacity-20"
								/>
								<circle
									cx="10"
									cy="10"
									r="8"
									fill="none"
									stroke="currentColor"
									stroke-width="2"
									stroke-linecap="round"
									stroke-dasharray="50.27"
									style={`stroke-dashoffset: ${contextCircleOffset};`}
								/>
							</svg>
						{/if}
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">Compact</span>
						{#if contextHasThreshold}
							<span class="app-muted text-[0.625rem] truncate shrink-0">
								{contextCirclePercent}% full
							</span>
						{/if}
					</span>
				</button>
			</Tooltip>
		{:else if item.data.id === 'fork'}
			<Tooltip content="Fork the current chat branch into a new chat." placement="top">
				<button
					type="button"
					aria-label="Fork: fork the current chat branch into a new chat."
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{commandIdx === selectedIdx ? 'app-interactive-active' : ''} disabled:opacity-50"
					disabled={forkDisabled}
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						if (!forkDisabled) {
							onSelect(item);
						}
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<svg
							class="size-3.5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M4 12H9" />
							<path d="M9 12C12.5 12 12.5 7 16 7H20" />
							<path d="M17 4L20 7L17 10" />
							<path d="M9 12C12.5 12 12.5 17 16 17H20" />
							<path d="M17 14L20 17L17 20" />
						</svg>
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">Fork</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">Current branch</span>
					</span>
				</button>
			</Tooltip>
		{:else if item.data.id === 'status'}
			<Tooltip content="Check what is running in this chat." placement="top">
				<button
					type="button"
					aria-label="Status: check what is running in this chat."
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{commandIdx === selectedIdx ? 'app-interactive-active' : ''}"
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						onSelect(item);
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<svg
							class="size-3.5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.75"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M12 14l4-4" />
							<path d="M3.34 19a10 10 0 1 1 17.32 0" />
						</svg>
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">Status</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							Check what is running in this chat.
						</span>
					</span>
				</button>
			</Tooltip>
		{:else if item.data.id === 'model'}
			<Tooltip content="Show or switch the current model." placement="top">
				<button
					type="button"
					aria-label="Model: show or switch the current model."
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{commandIdx === selectedIdx ? 'app-interactive-active' : ''}"
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						onSelect(item);
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<Sparkles className="size-3.5" />
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">Model</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">/model</span>
					</span>
				</button>
			</Tooltip>
		{:else if item.data.id === 'settings'}
			<Tooltip content="Open settings." placement="top">
				<button
					type="button"
					aria-label="Settings: open settings."
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{commandIdx === selectedIdx ? 'app-interactive-active' : ''}"
					on:mousedown={(e) => e.preventDefault()}
					on:click={() => {
						onSelect(item);
					}}
					on:mouseenter={() => {
						selectedIdx = commandIdx;
					}}
					on:focus={() => {}}
					data-selected={commandIdx === selectedIdx}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<Knobs className="size-3.5" />
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">Settings</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">/settings</span>
					</span>
				</button>
			</Tooltip>
		{/if}
	{/each}
{/if}
