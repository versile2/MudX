export function init(dotNetObjRef, container) {
    if (!container) return;

    const inputs = container.querySelectorAll("input");
    inputs.forEach((input) => {
        if (!input._pasteHandler) {
            input._pasteHandler = (event) => handlePaste(event, input, dotNetObjRef);
            input.addEventListener("paste", input._pasteHandler);
        }
    });
}

export function cleanup(container) {
    if (!container) return;

    const inputs = container.querySelectorAll("input");
    inputs.forEach((input) => {
        if (input._pasteHandler) {
            input.removeEventListener("paste", input._pasteHandler);
            delete input._pasteHandler;
        }
    });
}

function handlePaste(event, input, dotNetObjRef) {
    if (!event || !input || !dotNetObjRef) return;

    event.preventDefault();

    const paste = (event.clipboardData || window.clipboardData)?.getData("Text");
    if (paste) {
        dotNetObjRef.invokeMethodAsync("ClipboardPasteEvent", input.id, paste);
    }
}

export function focusBlock(container, inputId) {
    if (!container || !inputId) return;
    const input = container.querySelector("#" + inputId);
    if (input) {        
        try {
            input.focus();
            input.select(); // Select the input content if applicable
        }
        catch { }
    }
}
