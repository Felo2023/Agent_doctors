// Synthetic intake OCR helper. Receives one rendered PNG, emits JSON lines.
// Uses macOS Vision locally; does not transmit documents to a provider.
import Foundation
import ImageIO
import Vision

guard CommandLine.arguments.count == 2 else {
    fputs("Usage: vision_ocr.swift <png>\n", stderr)
    exit(2)
}
let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fputs("Unable to load image\n", stderr)
    exit(2)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["es-ES", "en-US"]
do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    let observations = (request.results ?? []).sorted {
        if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.015 {
            return $0.boundingBox.midY > $1.boundingBox.midY
        }
        return $0.boundingBox.minX < $1.boundingBox.minX
    }
    let lines: [[String: Any]] = observations.compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let rect = observation.boundingBox
        return [
            "text": candidate.string,
            "confidence": Double(candidate.confidence),
            "bbox": [rect.minX, rect.minY, rect.width, rect.height],
        ]
    }
    let output = try JSONSerialization.data(withJSONObject: ["lines": lines])
    FileHandle.standardOutput.write(output)
} catch {
    fputs("Vision OCR failed: \(error)\n", stderr)
    exit(1)
}
