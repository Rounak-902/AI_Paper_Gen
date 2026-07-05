import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';

export async function exportToPDF(
  elementId: string,
  filename: string = 'question-paper.pdf',
): Promise<void> {
  const element = document.getElementById(elementId);
  if (!element) {
    throw new Error(`Element with id "${elementId}" not found`);
  }

  // Hide action buttons during capture
  const actionElements = element.querySelectorAll('[data-no-print]');
  actionElements.forEach((el) => {
    (el as HTMLElement).style.display = 'none';
  });

  try {
    // Wait for all fonts (including KaTeX math fonts) to fully load
    await document.fonts.ready;

    const canvas = await html2canvas(element, {
      scale: 2,
      useCORS: true,
      logging: false,
      backgroundColor: '#ffffff',
      windowWidth: 794, // A4 width in px at 96dpi
    });

    const imgData = canvas.toDataURL('image/png');
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4',
    });

    const pdfWidth = pdf.internal.pageSize.getWidth();
    const pdfHeight = pdf.internal.pageSize.getHeight();
    const imgWidth = pdfWidth;
    const imgHeight = (canvas.height * pdfWidth) / canvas.width;

    let heightLeft = imgHeight;
    let position = 0;

    // First page
    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight);
    heightLeft -= pdfHeight;

    // Additional pages
    while (heightLeft > 0) {
      position = -(imgHeight - heightLeft);
      pdf.addPage();
      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight);
      heightLeft -= pdfHeight;
    }

    pdf.save(filename);
  } finally {
    // Restore action buttons
    actionElements.forEach((el) => {
      (el as HTMLElement).style.display = '';
    });
  }
}

export function printPaper(elementId: string, includeAnswerKey: boolean = false): void {
  const element = document.getElementById(elementId);
  if (!element) return;

  // Store current answer key visibility
  const answerKeyElements = element.querySelectorAll('[data-answer-key]');
  const originalDisplay: string[] = [];

  answerKeyElements.forEach((el, i) => {
    originalDisplay[i] = (el as HTMLElement).style.display;
    if (!includeAnswerKey) {
      (el as HTMLElement).style.display = 'none';
    } else {
      (el as HTMLElement).style.display = '';
    }
  });

  window.print();

  // Restore
  answerKeyElements.forEach((el, i) => {
    (el as HTMLElement).style.display = originalDisplay[i];
  });
}
