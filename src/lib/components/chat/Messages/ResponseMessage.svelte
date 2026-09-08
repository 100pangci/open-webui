<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { createEventDispatcher, onDestroy } from 'svelte';
	import { onMount, tick, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType, t } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	const dispatch = createEventDispatcher();

	import { config, models, settings, user } from '$lib/stores';
	import {
		copyToClipboard as _copyToClipboard,
		sanitizeResponseContent,
		formatMessageTimestamp,
		formatMessageTimestampFull,
		removeAllDetails
	} from '$lib/utils';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import equal from 'fast-deep-equal';

	import Name from './Name.svelte';
	import ProfileImage from './ProfileImage.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';

	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	import Error from './Error.svelte';
	import Citations from './Citations.svelte';
	import CodeExecutions from './CodeExecutions.svelte';
	import ContentRenderer from './ContentRenderer.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import FollowUps from './ResponseMessage/FollowUps.svelte';
	import { fade } from 'svelte/transition';
	import RegenerateMenu from './ResponseMessage/RegenerateMenu.svelte';
	import StatusHistory from './ResponseMessage/StatusHistory.svelte';
	import FullHeightIframe from '$lib/components/common/FullHeightIframe.svelte';
	import OutputEditView from './OutputEditView.svelte';
	import { getOutputText, replaceOutputMessageText, type OutputItem } from './structuredOutput';

	interface MessageType {
		id: string;
		model: string;
		content: string;
		output?: OutputItem[];
		files?: { type: string; url: string }[];
		timestamp: number;
		role: string;
		statusHistory?: {
			done: boolean;
			action: string;
			description: string;
			urls?: string[];
			query?: string;
		}[];
		status?: {
			done: boolean;
			action: string;
			description: string;
			urls?: string[];
			query?: string;
		};
		done: boolean;
		error?: boolean | { content: string };
		sources?: string[];
		code_executions?: {
			uuid: string;
			name: string;
			code: string;
			language?: string;
			result?: {
				error?: string;
				output?: string;
				files?: { name: string; url: string }[];
			};
		}[];
		info?: {
			openai?: boolean;
			prompt_tokens?: number;
			completion_tokens?: number;
			total_tokens?: number;
			eval_count?: number;
			eval_duration?: number;
			prompt_eval_count?: number;
			prompt_eval_duration?: number;
			total_duration?: number;
			load_duration?: number;
			usage?: unknown;
		};
	}

	export let chatId = '';
	export let history;
	export let messageId;
	export let selectedModels = [];

	let messageSource = history.messages[messageId];
	let message: MessageType = structuredClone(messageSource);
	$: if (history.messages) {
		const source = history.messages[messageId];
		if (source) {
			// Fast path for the fields that change most often while streaming.
			// Responses streams update output even when legacy content is unchanged.
			if (source !== messageSource) {
				messageSource = source;
				message = structuredClone(source);
			} else if (
				message.content !== source.content ||
				message.done !== source.done ||
				message.output?.length !== source.output?.length
			) {
				message = structuredClone(source);
			} else if (!equal(message, source)) {
				// Slow path: full comparison for infrequent changes (sources, annotations, status, etc.)
				message = structuredClone(source);
			}
		}
	}

	export let siblings;

	export let setInputText: Function = () => {};
	export let gotoMessage: Function = () => {};
	export let showPreviousMessage: Function;
	export let showNextMessage: Function;

	export let updateChat: Function;
	export let editMessage: Function;
	export let saveMessage: Function;
	export let rateMessage: Function;
	export let actionMessage: Function;
	export let deleteMessage: Function;

	export let submitMessage: Function;
	export let continueResponse: Function;
	export let regenerateResponse: Function;
	export let forkHandler: Function | null = null;

	export let addMessages: Function;
	export let onToolCallResolved: Function = () => {};

	export let isLastMessage = true;
	export let readOnly = false;
	export let allowDelete = true;
	export let compactPreview = false;
	export let editCodeBlock = true;
	export let topPadding = false;

	let citationsElement: HTMLDivElement;

	let contentContainerElement: HTMLDivElement;
	let buttonsContainerElement: HTMLDivElement;
	let showDeleteConfirm = false;

	let model = null;
	$: model = $models.find((m) => m.id === message.model);

	$: statusEntries = message?.statusHistory ?? [...(message?.status ? [message?.status] : [])];
	$: hasVisibleStatus =
		(model?.info?.meta?.capabilities?.status_updates ?? true) &&
		statusEntries.length > 0 &&
		!(statusEntries.at(-1)?.hidden ?? false);
	$: visibleResponseContent =
		getOutputText(message.output) || removeAllDetails(message.content ?? '');
	$: hasResponseContent = Boolean((message.content ?? '').trim() || message.output?.length);

	let edit = false;
	let editedContent = '';
	let editedOutput: any[] | null = null;
	let editTextAreaElement: HTMLTextAreaElement;

	let messageIndexEdit = false;

	const copyToClipboard = async (text) => {
		text = removeAllDetails(text);

		if (($config?.ui?.response_watermark ?? '').trim() !== '') {
			text = `${text}\n\n${$config?.ui?.response_watermark}`;
		}

		const res = await _copyToClipboard(text, null, $settings?.copyFormatted ?? false);
		if (res) {
			toast.success($i18n.t('Copying to clipboard was successful!'));
		}
	};

	let preprocessedDetailsCache = [];

	function preprocessForEditing(content: string): string {
		// Replace <details>...</details> with unique ID placeholder
		const detailsBlocks = [];
		let i = 0;

		content = content.replace(/<details[\s\S]*?<\/details>/gi, (match) => {
			detailsBlocks.push(match);
			return `<details id="__DETAIL_${i++}__"/>`;
		});

		// Store original blocks in the editedContent or globally (see merging later)
		preprocessedDetailsCache = detailsBlocks;

		return content;
	}

	function postprocessAfterEditing(content: string): string {
		const restoredContent = content.replace(
			/<details id="__DETAIL_(\d+)__"\/>/g,
			(_, index) => preprocessedDetailsCache[parseInt(index)] || ''
		);

		return restoredContent;
	}

	const editMessageHandler = async () => {
		edit = true;

		if (message.output?.length) {
			// Structured edit: use the block editor
			editedOutput = structuredClone(message.output);
		} else {
			// Legacy text edit: use the textarea
			editedContent = preprocessForEditing(message.content);
		}

		await tick();

		if (!editedOutput && editTextAreaElement) {
			const messagesContainer = document.getElementById('messages-container');
			const savedScrollTop = messagesContainer?.scrollTop ?? 0;

			editTextAreaElement.style.height = '';
			editTextAreaElement.style.height = `${editTextAreaElement.scrollHeight}px`;

			if (messagesContainer) messagesContainer.scrollTop = savedScrollTop;
			editTextAreaElement?.focus({ preventScroll: true });
		}
	};

	const editMessageConfirmHandler = async () => {
		if (editedOutput) {
			editMessage(message.id, { output: editedOutput }, false);
		} else {
			// Legacy text edit
			const messageContent = postprocessAfterEditing(editedContent ?? '');
			editMessage(message.id, { content: messageContent }, false);
		}

		edit = false;
		editedContent = '';
		editedOutput = null;

		await tick();
	};

	const saveAsCopyHandler = async () => {
		if (editedOutput) {
			editMessage(message.id, { output: editedOutput });
		} else {
			const messageContent = postprocessAfterEditing(editedContent ?? '');
			editMessage(message.id, { content: messageContent });
		}

		edit = false;
		editedContent = '';
		editedOutput = null;

		await tick();
	};

	const cancelEditMessage = async () => {
		edit = false;
		editedContent = '';
		editedOutput = null;
		await tick();
	};

	const deleteMessageHandler = async () => {
		deleteMessage(message.id);
	};

	$: if (!edit) {
		(async () => {
			await tick();
		})();
	}

	const buttonsWheelHandler = (event: WheelEvent) => {
		if (buttonsContainerElement) {
			if (buttonsContainerElement.scrollWidth <= buttonsContainerElement.clientWidth) {
				// If the container is not scrollable, horizontal scroll
				return;
			} else {
				event.preventDefault();

				if (event.deltaY !== 0) {
					// Adjust horizontal scroll position based on vertical scroll
					buttonsContainerElement.scrollLeft += event.deltaY;
				}
			}
		}
	};

	const contentCopyHandler = (e) => {
		if (contentContainerElement) {
			e.preventDefault();
			// Get the selected HTML
			const selection = window.getSelection();
			const range = selection.getRangeAt(0);
			const tempDiv = document.createElement('div');

			// Remove background, color, and font styles
			tempDiv.appendChild(range.cloneContents());

			tempDiv.querySelectorAll('table').forEach((table) => {
				table.style.borderCollapse = 'collapse';
				table.style.width = 'auto';
				table.style.tableLayout = 'auto';
			});

			tempDiv.querySelectorAll('th').forEach((th) => {
				th.style.whiteSpace = 'nowrap';
				th.style.padding = '4px 8px';
			});

			// Put cleaned HTML + plain text into clipboard
			e.clipboardData.setData('text/html', tempDiv.innerHTML);
			e.clipboardData.setData('text/plain', selection.toString());
		}
	};

	onMount(async () => {
		// console.log('ResponseMessage mounted');

		await tick();
		if (buttonsContainerElement) {
			buttonsContainerElement.addEventListener('wheel', buttonsWheelHandler);
		}

		if (contentContainerElement) {
			contentContainerElement.addEventListener('copy', contentCopyHandler);
		}
	});

	onDestroy(() => {
		if (buttonsContainerElement) {
			buttonsContainerElement.removeEventListener('wheel', buttonsWheelHandler);
		}

		if (contentContainerElement) {
			contentContainerElement.removeEventListener('copy', contentCopyHandler);
		}
	});
</script>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete message?')}
	on:confirm={() => {
		deleteMessageHandler();
	}}
/>

{#key message.id}
	<div
		class=" flex w-full message-{message.id}"
		id="message-{message.id}"
		dir={$settings.chatDirection}
		style="scroll-margin-top: 3rem;"
	>
		<div class={`shrink-0 ltr:mr-2 rtl:ml-2 hidden @lg:flex mt-0.5 `}>
			<ProfileImage
				src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${model?.id}&lang=${$i18n.language}`}
				className={'size-7 assistant-message-profile-image'}
			/>
		</div>

		<div class="flex-auto w-0 pl-1 relative">
			{#if !compactPreview}
				<Name>
					<Tooltip content={model?.name ?? message.model} placement="top-start">
						<span id="response-message-model-name" class="line-clamp-1 text-black dark:text-white">
							{model?.name ?? message.model}
						</span>
					</Tooltip>
				</Name>
			{/if}

			<div>
				<div class="chat-{message.role} w-full min-w-full">
					<div>
						{#if model?.info?.meta?.capabilities?.status_updates ?? true}
							<StatusHistory statusHistory={message?.statusHistory} />
						{/if}

						{#if message?.files && message.files?.filter( (f) => ['image', 'file'].includes(f.type) ).length > 0}
							<div
								class="my-1 w-full flex overflow-x-auto gap-2 flex-wrap"
								dir={$settings?.chatDirection ?? 'auto'}
							>
								{#each message.files.filter((f) => ['image', 'file'].includes(f.type)) as file}
									<div>
										{#if file.type === 'image' || (file?.content_type ?? '').startsWith('image/')}
											<Image src={file.url} alt={file.name || $i18n.t('Generated Image')} />
										{:else}
											<FileItem
												item={file}
												url={file.url}
												name={file.name}
												type={file.type}
												size={file?.size}
												small={true}
											/>
										{/if}
									</div>
								{/each}
							</div>
						{/if}

						{#if message?.embeds && message.embeds.length > 0}
							<div
								class="my-1 w-full flex overflow-x-auto gap-2 flex-wrap"
								id={`${message.id}-embeds-container`}
							>
								{#each message.embeds as embed, idx}
									<div class="my-2 w-full" id={`${message.id}-embeds-${idx}`}>
										<FullHeightIframe
											src={embed}
											allowScripts={true}
											allowForms={$settings?.iframeSandboxAllowForms ?? true}
											allowSameOrigin={$settings?.iframeSandboxAllowSameOrigin ?? false}
											allowPopups={true}
										/>
									</div>
								{/each}
							</div>
						{/if}

						{#if edit === true}
							<div
								class="w-full bg-gray-50 dark:bg-gray-800 rounded-3xl px-3 py-3 my-2 {($settings?.highContrastMode ??
								false)
									? 'focus-within:outline focus-within:outline-2 focus-within:-outline-offset-2 focus-within:outline-blue-500'
									: ''}"
							>
								{#if editedOutput}
									<!-- Structured output editor (visual + JSON toggle) -->
									<OutputEditView
										output={editedOutput}
										onChange={(updated) => {
											editedOutput = updated;
										}}
									/>
								{:else}
									<!-- Legacy textarea for messages without output -->
									<textarea
										id="message-edit-{message.id}"
										bind:this={editTextAreaElement}
										class=" bg-transparent outline-hidden focus-visible:outline-none! w-full resize-none text-[0.9375rem]"
										bind:value={editedContent}
										on:input={(e) => {
											const messagesContainer = document.getElementById('messages-container');
											const savedScrollTop = messagesContainer?.scrollTop ?? 0;
											const textarea = e.currentTarget;

											textarea.style.height = '';
											textarea.style.height = `${textarea.scrollHeight}px`;

											if (messagesContainer) messagesContainer.scrollTop = savedScrollTop;
										}}
										on:keydown={(e) => {
											if (e.key === 'Escape') {
												document.getElementById('close-edit-message-button')?.click();
											}

											const isCmdOrCtrlPressed = e.metaKey || e.ctrlKey;
											const isEnterPressed = e.key === 'Enter';

											if (isCmdOrCtrlPressed && isEnterPressed) {
												document.getElementById('confirm-edit-message-button')?.click();
											}
										}}
									></textarea>
								{/if}

								<div class=" mt-2 flex justify-between text-sm font-normal">
									<div>
										<button
											id="save-new-message-button"
											class="px-2.5 py-1 bg-gray-50 hover:bg-gray-100 dark:bg-gray-800 dark:hover:bg-gray-700 border border-gray-100 dark:border-gray-700 text-gray-700 dark:text-gray-200 transition rounded-3xl"
											on:click={() => {
												saveAsCopyHandler();
											}}
										>
											{$i18n.t('Save As Copy')}
										</button>
									</div>

									<div class="flex space-x-1.5">
										<button
											id="close-edit-message-button"
											class="px-2.5 py-1 bg-white dark:bg-gray-900 hover:bg-gray-100 text-gray-800 dark:text-gray-100 transition rounded-3xl"
											on:click={() => {
												cancelEditMessage();
											}}
										>
											{$i18n.t('Cancel')}
										</button>

										<button
											id="confirm-edit-message-button"
											class="px-2.5 py-1 bg-gray-900 dark:bg-white hover:bg-gray-850 text-gray-100 dark:text-gray-800 transition rounded-3xl"
											on:click={() => {
												editMessageConfirmHandler();
											}}
										>
											{$i18n.t('Save')}
										</button>
									</div>
								</div>
							</div>
						{/if}

						<div
							bind:this={contentContainerElement}
							class="w-full flex flex-col relative {edit ? 'hidden' : ''}"
							id="response-content-container"
						>
							{#if hasResponseContent && message.error !== true}
								<!-- always show message contents even if there's an error -->
								<!-- unless message.error === true which is legacy error handling, where the error message is stored in message.content -->
								<ContentRenderer
									id={`${chatId}-${message.id}`}
									{chatId}
									messageId={message.id}
									content={message.content}
									output={message.output}
									sources={message.sources}
									floatingButtons={message?.done &&
										!readOnly &&
										($settings?.showFloatingActionButtons ?? true)}
									save={!readOnly}
									preview={!readOnly}
									{compactPreview}
									{editCodeBlock}
									{topPadding}
									done={message?.done ?? false}
									{model}
									onTaskClick={async (e) => {
										console.log(e);
									}}
									{onToolCallResolved}
									onSourceClick={async (id) => {
										console.log(id);

										if (citationsElement) {
											citationsElement?.showSourceModal(id);
										}
									}}
									onSetInputText={(text) => {
										setInputText(text);
									}}
									onSave={({ raw, oldContent, newContent }) => {
										const sourceMessage = history.messages[message.id];
										if (sourceMessage.output?.length) {
											const updatedOutput = replaceOutputMessageText(
												sourceMessage.output,
												oldContent,
												newContent
											);
											if (updatedOutput !== sourceMessage.output) {
												sourceMessage.output = updatedOutput;
											} else {
												sourceMessage.content = sourceMessage.content.replace(
													raw,
													raw.replace(oldContent, newContent)
												);
											}
										} else {
											sourceMessage.content = sourceMessage.content.replace(
												raw,
												raw.replace(oldContent, newContent)
											);
										}

										updateChat();
									}}
								/>
							{/if}

							{#if !message.done && !message.error && (hasResponseContent || !hasVisibleStatus)}
								<div class="text-[0.9375rem] leading-relaxed">
									<span
										class="inline-block w-[0.125rem] h-3.5 bg-gray-400 dark:bg-gray-500 ml-0.5 animate-pulse align-text-bottom"
									></span>
								</div>
							{/if}

							{#if message?.error}
								<Error content={message?.error?.content ?? message.content} />
							{/if}

							{#if (message?.sources || message?.citations) && (model?.info?.meta?.capabilities?.citations ?? true)}
								<Citations
									bind:this={citationsElement}
									id={message?.id}
									{chatId}
									sources={message?.sources ?? message?.citations}
									{readOnly}
								/>
							{/if}

							{#if message.code_executions}
								<CodeExecutions codeExecutions={message.code_executions} />
							{/if}
						</div>
					</div>
				</div>

				{#if compactPreview && message.timestamp}
					<div class="mt-0.5 flex justify-start whitespace-nowrap text-gray-600 dark:text-gray-500">
						<Tooltip
							className="flex self-center"
							content={formatMessageTimestampFull(message.timestamp * 1000)}
							placement="bottom"
						>
							<time
								datetime={new Date(message.timestamp * 1000).toISOString()}
								class="ml-1 shrink-0 whitespace-nowrap text-[0.6875rem] tabular-nums text-gray-400 dark:text-gray-600 select-none"
							>
								{formatMessageTimestamp(message.timestamp * 1000)}
							</time>
						</Tooltip>
					</div>
				{:else if !edit}
					<div
						bind:this={buttonsContainerElement}
						class="flex items-center justify-start overflow-x-auto whitespace-nowrap buttons text-gray-600 dark:text-gray-500 mt-0.5 [&>*]:shrink-0"
					>
						{#if message.done || siblings.length > 1}
							{#if siblings.length > 1}
								<div class="flex self-center min-w-fit" dir="ltr">
									<button
										aria-label={$i18n.t('Previous message')}
										class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-md transition"
										on:click={() => {
											showPreviousMessage(message);
										}}
									>
										<svg
											aria-hidden="true"
											xmlns="http://www.w3.org/2000/svg"
											fill="none"
											viewBox="0 0 24 24"
											stroke="currentColor"
											stroke-width="2.5"
											class="size-3.5"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												d="M15.75 19.5 8.25 12l7.5-7.5"
											/>
										</svg>
									</button>

									{#if messageIndexEdit}
										<div
											class="text-sm flex justify-center font-normal self-center dark:text-gray-100 min-w-fit"
										>
											<input
												id="message-index-input-{message.id}"
												type="number"
												value={siblings.indexOf(message.id) + 1}
												min="1"
												max={siblings.length}
												on:focus={(e) => {
													e.target.select();
												}}
												on:blur={(e) => {
													gotoMessage(message, e.target.value - 1);
													messageIndexEdit = false;
												}}
												on:keydown={(e) => {
													if (e.key === 'Enter') {
														gotoMessage(message, e.target.value - 1);
														messageIndexEdit = false;
													}
												}}
												class="bg-transparent font-normal self-center dark:text-gray-100 min-w-fit outline-hidden"
											/>/{siblings.length}
										</div>
									{:else}
										<!-- svelte-ignore a11y-no-static-element-interactions -->
										<div
											class="text-sm tracking-widest font-normal self-center dark:text-gray-100 min-w-fit"
											on:dblclick={async () => {
												messageIndexEdit = true;

												await tick();
												const input = document.getElementById(`message-index-input-${message.id}`);
												if (input) {
													input.focus();
													input.select();
												}
											}}
										>
											{siblings.indexOf(message.id) + 1}/{siblings.length}
										</div>
									{/if}

									<button
										class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-md transition"
										on:click={() => {
											showNextMessage(message);
										}}
										aria-label={$i18n.t('Next message')}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											fill="none"
											aria-hidden="true"
											viewBox="0 0 24 24"
											stroke="currentColor"
											stroke-width="2.5"
											class="size-3.5"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												d="m8.25 4.5 7.5 7.5-7.5 7.5"
											/>
										</svg>
									</button>
								</div>
							{/if}

							{#if message.done}
								{#if !readOnly}
									{#if $user?.role === 'user' ? ($user?.permissions?.chat?.edit ?? true) : true}
										<Tooltip content={$i18n.t('Edit')} placement="bottom">
											<button
												aria-label={$i18n.t('Edit')}
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition"
												on:click={() => {
													editMessageHandler();
												}}
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													fill="none"
													viewBox="0 0 24 24"
													stroke-width="2.3"
													aria-hidden="true"
													stroke="currentColor"
													class="w-4 h-4"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L6.832 19.82a4.5 4.5 0 01-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 011.13-1.897L16.863 4.487zm0 0L19.5 7.125"
													/>
												</svg>
											</button>
										</Tooltip>
									{/if}
								{/if}

								<Tooltip content={$i18n.t('Copy')} placement="bottom">
									<button
										aria-label={$i18n.t('Copy')}
										class="{isLastMessage || ($settings?.highContrastMode ?? false)
											? 'visible'
											: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition copy-response-button"
										on:click={() => {
											copyToClipboard(visibleResponseContent);
										}}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											fill="none"
											aria-hidden="true"
											viewBox="0 0 24 24"
											stroke-width="2.3"
											stroke="currentColor"
											class="w-4 h-4"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184"
											/>
										</svg>
									</button>
								</Tooltip>

								{#if message.usage}
									<Tooltip
										content={message.usage
											? `<pre>${sanitizeResponseContent(
													JSON.stringify(message.usage, null, 2)
														.replace(/"([^(")"]+)":/g, '$1:')
														.slice(1, -1)
														.split('\n')
														.map((line) => line.slice(2))
														.map((line) => (line.endsWith(',') ? line.slice(0, -1) : line))
														.join('\n')
												)}</pre>`
											: ''}
										placement="bottom"
									>
										<button
											aria-hidden="true"
											class=" {isLastMessage || ($settings?.highContrastMode ?? false)
												? 'visible'
												: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition whitespace-pre-wrap"
											on:click={() => {
												console.log(message);
											}}
											id="info-{message.id}"
										>
											<svg
												aria-hidden="true"
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
												stroke-width="2.3"
												stroke="currentColor"
												class="w-4 h-4"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z"
												/>
											</svg>
										</button>
									</Tooltip>
								{/if}

								{#if !readOnly}
									{#if isLastMessage && ($user?.role === 'admin' || ($user?.permissions?.chat?.continue_response ?? true))}
										<Tooltip content={$i18n.t('Continue Response')} placement="bottom">
											<button
												aria-label={$i18n.t('Continue Response')}
												type="button"
												id="continue-response-button"
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition"
												on:click={() => {
													continueResponse();
												}}
											>
												<svg
													aria-hidden="true"
													xmlns="http://www.w3.org/2000/svg"
													fill="none"
													viewBox="0 0 24 24"
													stroke-width="2.3"
													stroke="currentColor"
													class="w-4 h-4"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
													/>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														d="M15.91 11.672a.375.375 0 0 1 0 .656l-5.603 3.113a.375.375 0 0 1-.557-.328V8.887c0-.286.307-.466.557-.327l5.603 3.112Z"
													/>
												</svg>
											</button>
										</Tooltip>
									{/if}

									{#if $user?.role === 'admin' || ($user?.permissions?.chat?.regenerate_response ?? true)}
										{#if $settings?.regenerateMenu ?? true}
											<button
												type="button"
												class="hidden regenerate-response-button"
												on:click={() => {
													regenerateResponse(message);

													(model?.actions ?? []).forEach((action) => {
														dispatch('action', {
															id: action.id,
															event: {
																id: 'regenerate-response',
																data: {
																	messageId: message.id
																}
															}
														});
													});
												}}
											/>

											<RegenerateMenu
												onRegenerate={(prompt = null) => {
													regenerateResponse(message, prompt);

													(model?.actions ?? []).forEach((action) => {
														dispatch('action', {
															id: action.id,
															event: {
																id: 'regenerate-response',
																data: {
																	messageId: message.id
																}
															}
														});
													});
												}}
											>
												<Tooltip content={$i18n.t('Regenerate')} placement="bottom">
													<button
														type="button"
														aria-label={$i18n.t('Regenerate')}
														class="{isLastMessage || ($settings?.highContrastMode ?? false)
															? 'visible'
															: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition"
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															fill="none"
															viewBox="0 0 24 24"
															stroke-width="2.3"
															aria-hidden="true"
															stroke="currentColor"
															class="w-4 h-4"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
															/>
														</svg>
													</button>
												</Tooltip>
											</RegenerateMenu>
										{:else}
											<Tooltip content={$i18n.t('Regenerate')} placement="bottom">
												<button
													type="button"
													aria-label={$i18n.t('Regenerate')}
													class="{isLastMessage || ($settings?.highContrastMode ?? false)
														? 'visible'
														: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition regenerate-response-button"
													on:click={() => {
														regenerateResponse(message);

														(model?.actions ?? []).forEach((action) => {
															dispatch('action', {
																id: action.id,
																event: {
																	id: 'regenerate-response',
																	data: {
																		messageId: message.id
																	}
																}
															});
														});
													}}
												>
													<svg
														xmlns="http://www.w3.org/2000/svg"
														fill="none"
														viewBox="0 0 24 24"
														stroke-width="2.3"
														aria-hidden="true"
														stroke="currentColor"
														class="w-4 h-4"
													>
														<path
															stroke-linecap="round"
															stroke-linejoin="round"
															d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
														/>
													</svg>
												</button>
											</Tooltip>
										{/if}
									{/if}

									{#each model?.actions ?? [] as action}
										<Tooltip content={action.name} placement="bottom">
											<button
												type="button"
												aria-label={action.name}
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition"
												on:click={() => {
													actionMessage(action.id, message);
												}}
											>
												{#if action?.icon}
													<div class="size-4">
														<img
															src={action.icon}
															class="w-4 h-4 {action.icon.includes('data:image/svg')
																? 'dark:invert-[80%]'
																: ''}"
															style="fill: currentColor;"
															alt={action.name}
															draggable="false"
														/>
													</div>
												{:else}
													<Sparkles strokeWidth="2.1" className="size-4" />
												{/if}
											</button>
										</Tooltip>
									{/each}

									{#if message.done && !readOnly && forkHandler && ($user?.role === 'admin' || ($user?.permissions?.chat?.import ?? true))}
										<Tooltip content="Fork chat" placement="bottom">
											<button
												aria-label="Fork chat"
												class="{isLastMessage || ($settings?.highContrastMode ?? false)
													? 'visible'
													: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition"
												on:click={() => {
													forkHandler?.(message.id);
												}}
											>
												<svg
													class="w-4 h-4"
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
											</button>
										</Tooltip>
									{/if}

									{#if $user?.role === 'admin' || ($user?.permissions?.chat?.delete_message ?? true)}
										{#if siblings.length > 1}
											<Tooltip content={$i18n.t('Delete')} placement="bottom">
												<button
													type="button"
													aria-label={$i18n.t('Delete')}
													id="delete-response-button"
													class="{isLastMessage || ($settings?.highContrastMode ?? false)
														? 'visible'
														: 'hover-reveal'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition disabled:opacity-50 disabled:hover:bg-transparent"
													disabled={!allowDelete}
													on:click={(e) => {
														if (!allowDelete) {
															return;
														}
														if (e.shiftKey) {
															deleteMessageHandler();
														} else {
															showDeleteConfirm = true;
														}
													}}
												>
													<svg
														xmlns="http://www.w3.org/2000/svg"
														fill="none"
														viewBox="0 0 24 24"
														stroke-width="2"
														stroke="currentColor"
														aria-hidden="true"
														class="w-4 h-4"
													>
														<path
															stroke-linecap="round"
															stroke-linejoin="round"
															d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
														/>
													</svg>
												</button>
											</Tooltip>
										{/if}
									{/if}

									{#if message.timestamp}
										<Tooltip
											className="flex self-center"
											content={formatMessageTimestampFull(message.timestamp * 1000)}
											placement="bottom"
										>
											<time
												datetime={new Date(message.timestamp * 1000).toISOString()}
												class="hover-reveal ml-1 shrink-0 whitespace-nowrap text-[0.6875rem] tabular-nums text-gray-400 dark:text-gray-600 select-none"
											>
												{formatMessageTimestamp(message.timestamp * 1000)}
											</time>
										</Tooltip>
									{/if}
								{/if}
							{/if}
						{/if}
					</div>

					{#if (isLastMessage || ($settings?.keepFollowUpPrompts ?? false)) && message.done && !readOnly && (message?.followUps ?? []).length > 0}
						<div class="my-2.5" in:fade={{ duration: 100 }}>
							<FollowUps
								followUps={message?.followUps}
								onClick={(prompt) => {
									if ($settings?.insertFollowUpPrompt ?? false) {
										// Insert the follow-up prompt into the input box
										setInputText(prompt);
									} else {
										// Submit the follow-up prompt directly
										submitMessage(message?.id, prompt);
									}
								}}
							/>
						</div>
					{/if}
				{/if}
			</div>
		</div>
	</div>
{/key}

<style>
	.buttons::-webkit-scrollbar {
		display: none; /* for Chrome, Safari and Opera */
	}

	.buttons {
		-ms-overflow-style: none; /* IE and Edge */
		scrollbar-width: none; /* Firefox */
	}
</style>
