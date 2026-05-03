package com.archon.openmetadata.metadata.controllers;

import com.archon.openmetadata.metadata.dto.ColumnEntityDto;
import com.archon.openmetadata.metadata.dto.ColumnEntityFilterBean;
import com.archon.openmetadata.metadata.models.ColumnEntity;
import com.archon.openmetadata.metadata.services.ColumnEntityService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.persistence.criteria.Predicate;
import lombok.RequiredArgsConstructor;
import org.modelmapper.ModelMapper;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.data.web.PagedResourcesAssembler;
import org.springframework.hateoas.EntityModel;
import org.springframework.hateoas.PagedModel;
import org.springframework.hateoas.server.mvc.WebMvcLinkBuilder;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/columns")
@RequiredArgsConstructor
@CrossOrigin(origins = "*")
public class ColumnEntityController {

  private final ColumnEntityService service;
  private final ModelMapper modelMapper;
  private final PagedResourcesAssembler<ColumnEntity> pagedResourcesAssembler;

  @GetMapping
  public ResponseEntity<PagedModel<EntityModel<ColumnEntityDto>>> fetchAll(Pageable pageable) {
    Page<ColumnEntity> page = service.findAll(Specification.where(null), pageable);
    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, ColumnEntityDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(ColumnEntityController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }

  @GetMapping("/{id}")
  public ResponseEntity<EntityModel<ColumnEntityDto>> getById(@PathVariable UUID id) {
    ColumnEntity entity = service.findById(id);
    if (entity == null) return ResponseEntity.notFound().build();
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(entity, ColumnEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(ColumnEntityController.class).getById(id))
                .withSelfRel()));
  }

  @PostMapping
  public ResponseEntity<EntityModel<ColumnEntityDto>> create(@RequestBody ColumnEntityDto dto) {
    ColumnEntity saved = service.save(modelMapper.map(dto, ColumnEntity.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(saved, ColumnEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(ColumnEntityController.class).getById(saved.getId()))
                .withSelfRel()));
  }

  @PutMapping("/{id}")
  public ResponseEntity<EntityModel<ColumnEntityDto>> update(
      @PathVariable UUID id, @RequestBody ColumnEntityDto dto) {
    dto.setId(id);
    ColumnEntity updated = service.save(modelMapper.map(dto, ColumnEntity.class));
    return ResponseEntity.ok(
        EntityModel.of(
            modelMapper.map(updated, ColumnEntityDto.class),
            WebMvcLinkBuilder.linkTo(
                    WebMvcLinkBuilder.methodOn(ColumnEntityController.class)
                        .getById(updated.getId()))
                .withSelfRel()));
  }

  @DeleteMapping("/{id}")
  public ResponseEntity<Void> delete(@PathVariable UUID id) {
    service.deleteById(id);
    return ResponseEntity.ok().build();
  }

  @PostMapping("/search")
  public ResponseEntity<PagedModel<EntityModel<ColumnEntityDto>>> searchAll(
      @RequestBody ColumnEntityFilterBean filterBean, Pageable pageable) {
    Page<ColumnEntity> page =
        service.findAll(
            (root, query, criteriaBuilder) -> {
              List<Predicate> predicates = new ArrayList<>();

              // Example of inline explicit criteria matching your requirement
              if (filterBean.getSearchText() != null && !filterBean.getSearchText().isEmpty()) {
                // Replace "id" with actual string fields you want to search
                predicates.add(
                    criteriaBuilder.like(
                        root.get("id").as(String.class), "%" + filterBean.getSearchText() + "%"));
              }

              if (predicates.isEmpty()) {
                return criteriaBuilder.conjunction();
              }
              return criteriaBuilder.and(predicates.toArray(new Predicate[0]));
            },
            pageable);

    return ResponseEntity.ok(
        pagedResourcesAssembler.toModel(
            page,
            entity ->
                EntityModel.of(
                    modelMapper.map(entity, ColumnEntityDto.class),
                    WebMvcLinkBuilder.linkTo(
                            WebMvcLinkBuilder.methodOn(ColumnEntityController.class)
                                .getById(entity.getId()))
                        .withSelfRel())));
  }
}
