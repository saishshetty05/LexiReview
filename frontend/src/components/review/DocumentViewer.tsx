import { useEffect, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import mammoth from "mammoth";
import { ChevronLeft, ChevronRight, FileWarning } from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useDocumentFileQuery } from "@/hooks/queries";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const DOCX_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function DocxViewer({ blob }: { blob: Blob }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    blob
      .arrayBuffer()
      .then((buffer) => mammoth.convertToHtml({ arrayBuffer: buffer }))
      .then((result) => {
        if (!cancelled) setHtml(result.value);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [blob]);

  if (error) {
    return <ViewerMessage text="Could not render this document." />;
  }
  if (html === null) {
    return <ViewerMessage text="Rendering document..." />;
  }
  return (
    <div
      className="prose prose-sm max-w-none dark:prose-invert"
      // Content is the user's own uploaded document, converted from DOCX
      // XML to structured HTML by mammoth (not arbitrary attacker HTML
      // passed through verbatim) -- same trust boundary as any other
      // document-preview feature rendering a user's own file back to them.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function PdfViewer({ blob }: { blob: Blob }) {
  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);

  if (!url) return <ViewerMessage text="Rendering document..." />;

  return (
    <div className="flex h-full flex-col">
      <ScrollArea className="flex-1">
        <div className="flex justify-center p-4">
          <Document
            file={url}
            onLoadSuccess={({ numPages: total }) => setNumPages(total)}
            loading={<ViewerMessage text="Rendering document..." />}
            error={<ViewerMessage text="Could not render this document." />}
          >
            <Page pageNumber={pageNumber} width={520} />
          </Document>
        </div>
      </ScrollArea>
      {numPages !== null && numPages > 1 && (
        <div className="flex items-center justify-center gap-3 border-t p-2">
          <Button
            variant="ghost"
            size="icon"
            disabled={pageNumber <= 1}
            onClick={() => setPageNumber((page) => page - 1)}
          >
            <ChevronLeft />
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {pageNumber} of {numPages}
          </span>
          <Button
            variant="ghost"
            size="icon"
            disabled={pageNumber >= numPages}
            onClick={() => setPageNumber((page) => page + 1)}
          >
            <ChevronRight />
          </Button>
        </div>
      )}
    </div>
  );
}

function ViewerMessage({ text }: { text: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
      <FileWarning className="h-6 w-6" />
      {text}
    </div>
  );
}

export function DocumentViewer({ docId }: { docId: string | null }) {
  // docId is threaded through navigate(...,{state}) from the upload
  // redirect, same limitation ReviewPage has always had for the summary
  // fetch: a direct visit/refresh loses it, and the viewer just can't
  // render in that case (same "optional, degrade quietly" treatment).
  const { data, isLoading, isError } = useDocumentFileQuery(docId, !!docId);

  if (!docId) {
    return (
      <ViewerMessage text="Document preview isn't available after a page refresh or direct link -- only right after upload." />
    );
  }
  if (isLoading) return <ViewerMessage text="Loading document..." />;
  if (isError || !data) return <ViewerMessage text="Could not load this document." />;

  if (data.contentType === DOCX_CONTENT_TYPE) {
    return (
      <ScrollArea className="h-full">
        <div className="p-6">
          <DocxViewer blob={data.blob} />
        </div>
      </ScrollArea>
    );
  }
  return <PdfViewer blob={data.blob} />;
}
