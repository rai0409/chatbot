import { useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import { extractionApi, type PageData } from "../../api/extraction";
import { jaExtraction as x } from "../../i18n/extraction";

interface Props {
  tenant: string;
  page: PageData;
  selectedId: string;
}

type ImageState = { kind: "loading" } | { kind: "ready"; url: string } | { kind: "none" };

/** The page image with a box on every line that has a position. Weak lines are red, the selected line is thick orange. */
export function PageViewer({ tenant, page, selectedId }: Props) {
  const [image, setImage] = useState<ImageState>(page.preview_available ? { kind: "loading" } : { kind: "none" });

  useEffect(() => {
    if (!page.preview_available) {
      setImage({ kind: "none" });
      return;
    }
    let cancelled = false;
    let objectUrl = "";
    setImage({ kind: "loading" });
    extractionApi
      .pageImage(tenant, page.source_doc, page.page)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setImage({ kind: "ready", url: objectUrl });
      })
      .catch((e) => {
        if (!cancelled && !(e instanceof ApiError && e.status === 401)) setImage({ kind: "none" });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [tenant, page.source_doc, page.page, page.preview_available]);

  if (image.kind === "loading") return <p role="status" className="muted">{x.imageLoading}</p>;
  if (image.kind === "none") return <p className="muted">{x.imageUnavailable}</p>;

  const w = page.width_px;
  const h = page.height_px;
  return (
    <>
      <div className="ex-stage">
        <img src={image.url} alt={x.imageAlt(page.page)} />
        {w > 0 && h > 0
          ? page.lines.map((line) => {
              if (!line.bbox || line.bbox.length < 4) return null;
              const weak = line.confidence == null || line.confidence < page.threshold;
              const cls = "ex-box" + (weak ? " weak" : "") + (line.id === selectedId ? " sel" : "");
              return (
                <div
                  key={line.id}
                  className={cls}
                  data-testid={`box-${line.id}`}
                  title={line.text}
                  style={{
                    left: `${(line.bbox[0] / w) * 100}%`,
                    top: `${(line.bbox[1] / h) * 100}%`,
                    width: `${((line.bbox[2] - line.bbox[0]) / w) * 100}%`,
                    height: `${((line.bbox[3] - line.bbox[1]) / h) * 100}%`,
                  }}
                />
              );
            })
          : null}
      </div>
      <p className="muted">{x.boxHelp}</p>
    </>
  );
}
